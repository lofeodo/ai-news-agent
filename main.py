# main.py
#
# Shared Cloud Run entrypoint for all agents.
# Reads AGENT_NAME env var to decide which agent to run.
#
# Valid values:
#   agent1a      → agents/agent1a_fetch_papers.py
#   agent1b      → agents/agent1b_fetch_news.py
#   agent2a      → agents/agent2a_summarize_papers.py
#   agent2b      → agents/agent2b_summarize_news.py
#   agent3       → agents/agent3_compose.py
#   agent4       → agents/agent4_send.py
#   orchestrator → orchestrator.py
#   healthcheck  → agents/agent_healthcheck.py
#
#   agent_subscriptions → agents/agent_subscriptions.py
#     Special case: NOT in AGENT_REGISTRY because it has no run(run_id).
#     It's a synchronous request/response API — its routes are mounted
#     onto this app below, only when AGENT_NAME selects it.
#
# Cloud Run invokes the pipeline agents via HTTP POST to /.
# The agent runs in a background thread so we can return 200 immediately —
# Cloud Run has a request timeout, but our agents can take several minutes.

import base64
import json
import os
import sys
import threading
import traceback
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    _CUTOFF_TZ = ZoneInfo("America/Toronto")
except Exception:  # pragma: no cover - zoneinfo/tzdata missing
    _CUTOFF_TZ = timezone(timedelta(hours=-5))  # fallback: fixed EST offset

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Make the agents/ directory importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "agents"))

app = FastAPI()


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # /preview sets its own frame-ancestors CSP and must be embeddable
        if request.url.path != "/preview":
            response.headers["X-Frame-Options"] = "DENY"
        return response


app.add_middleware(_SecurityHeadersMiddleware)

AGENT_REGISTRY = {
    "agent1a":      "agent1a_fetch_papers",
    "agent1b":      "agent1b_fetch_news",
    "agent2a":      "agent2a_summarize_papers",
    "agent2b":      "agent2b_summarize_news",
    "agent3":       "agent3_compose",
    "agent4":       "agent4_send",
    "orchestrator": "orchestrator",
    "healthcheck":  "agent_healthcheck",
}

# ── Subscription service ─────────────────────────────────────────────────────
# Mounted only in the agent_subscriptions container, so the pipeline agents
# never expose subscription routes. CORS is needed here (and nowhere else)
# because browsers on the frontend origin call this API directly.
if os.environ.get("AGENT_NAME", "").strip() == "agent_subscriptions":
    from fastapi.middleware.cors import CORSMiddleware
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from agent_subscriptions import router as subscriptions_router, limiter

    # Comma-separated list of allowed frontend origins, e.g.
    # "https://latentspacemail.web.app,https://latentspacemail.com"
    # No default wildcard — must be set explicitly. Falls back to "*" only
    # when USE_FIRESTORE is false (local dev).
    _origins_env = os.environ.get("ALLOWED_ORIGINS", "")
    origins = [o.strip() for o in _origins_env.split(",") if o.strip()]
    if not origins:
        _use_firestore = os.environ.get("USE_FIRESTORE", "false").lower() == "true"
        if _use_firestore:
            raise RuntimeError(
                "ALLOWED_ORIGINS must be set in production (USE_FIRESTORE=true). "
                "Example: https://latentspacemail.web.app"
            )
        origins = ["*"]  # local dev only

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(subscriptions_router)


# ── Hard runtime limits ──────────────────────────────────────────────────────
# No agent may run forever. Two ceilings, whichever comes first:
#   1. MAX_RUNTIME_SECONDS after the agent starts (a flat 1-hour cap).
#   2. The daily 07:30 America/Toronto cutoff — but only for a run that starts
#      before it. The scheduled pipeline (06:00) and send (07:00) must be
#      settled before subscribers are awake. A run started AFTER 07:30 (a
#      manual daytime re-run / recovery) is bounded only by ceiling 1.
# On expiry the watchdog records {agent}_error to the run's Firestore doc
# (so status doesn't stay "running" and the health check can name a cause)
# and hard-exits the process — os._exit works even if the agent thread is
# wedged in a C extension, which a Python-level timeout would not survive.
MAX_RUNTIME_SECONDS = 3600
_HARD_CUTOFF_HOUR = 7
_HARD_CUTOFF_MINUTE = 30


def _deadline_seconds() -> float:
    """Seconds from now until this agent must be dead."""
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(seconds=MAX_RUNTIME_SECONDS)

    now_local = now.astimezone(_CUTOFF_TZ)
    cutoff_local = now_local.replace(
        hour=_HARD_CUTOFF_HOUR, minute=_HARD_CUTOFF_MINUTE, second=0, microsecond=0
    )
    if now_local < cutoff_local:
        deadline = min(deadline, cutoff_local.astimezone(timezone.utc))

    return max(1.0, (deadline - now).total_seconds())


def _update_run_doc(run_id: str, fields_fn) -> None:
    """Best-effort merge-write to the run's Firestore doc. fields_fn receives the
    firestore module (for sentinels like DELETE_FIELD) and returns the fields."""
    if os.environ.get("USE_FIRESTORE", "false").lower() != "true":
        return
    try:
        from google.cloud import firestore
        from config import GCP_PROJECT_ID, FIRESTORE_COLLECTION

        firestore.Client(project=GCP_PROJECT_ID).collection(
            FIRESTORE_COLLECTION
        ).document(run_id).set(fields_fn(firestore), merge=True)
    except Exception:
        sys.stderr.write(f"[main]  failed to update run doc {run_id} in Firestore:\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()


def _record_failure(agent_name: str, run_id: str, error: str) -> None:
    _update_run_doc(run_id, lambda fs: {
        f"{agent_name}_error": error,
        f"{agent_name}_failed_at": datetime.now(timezone.utc).isoformat(),
    })


# ── Crash isolation + retry ──────────────────────────────────────────────────
# Agents listed here run in a child process instead of the server's own thread,
# and are re-run up to N more times if the child exits unsuccessfully. A child
# process is the only way to survive a native abort: agent2b has twice died
# with a heap-corruption SIGABRT inside C extension code (2026-09-07,
# 2026-09-28), which kills the whole interpreter — no in-process try/except or
# retry loop outlives it. The child's stdout/stderr are inherited, so its logs
# land in Cloud Logging exactly as before. All attempts together stay under the
# single watchdog deadline armed in _run_agent.
_ISOLATED_RETRIES = {
    "agent2b": 5,
}
_RETRY_DELAY_SECONDS = 5

_CHILD_CODE = (
    "import importlib, sys; sys.path.insert(0, sys.argv[1]); "
    "importlib.import_module(sys.argv[2]).run(sys.argv[3])"
)


def _describe_exit(returncode: int) -> str:
    if returncode < 0:
        import signal
        try:
            name = signal.Signals(-returncode).name
        except ValueError:
            name = f"signal {-returncode}"
        return f"killed by {name}"
    return f"exit code {returncode}"


def _run_isolated(agent_name: str, module_name: str, run_id: str, retries: int, current: dict) -> None:
    import subprocess
    import time

    app_dir = os.path.dirname(os.path.abspath(__file__))
    agents_dir = os.path.join(app_dir, "agents")
    attempts = retries + 1

    for attempt in range(1, attempts + 1):
        print(f"[main]  {agent_name} attempt {attempt}/{attempts} (child process, run_id={run_id})", flush=True)
        proc = subprocess.Popen(
            [sys.executable, "-c", _CHILD_CODE, agents_dir, module_name, run_id],
            cwd=app_dir,
        )
        current["proc"] = proc
        returncode = proc.wait()
        current["proc"] = None

        if returncode == 0:
            print(f"[main]  {module_name} completed successfully on attempt {attempt}/{attempts}", flush=True)
            if attempt > 1:
                # A failed earlier attempt may have written {agent}_error; clear
                # it so the health check doesn't flag a run that recovered.
                _update_run_doc(run_id, lambda fs: {
                    f"{agent_name}_error": fs.DELETE_FIELD,
                    f"{agent_name}_failed_at": fs.DELETE_FIELD,
                    f"{agent_name}_attempts": attempt,
                })
            return

        outcome = _describe_exit(returncode)
        sys.stderr.write(f"[main]  {agent_name} attempt {attempt}/{attempts} failed ({outcome})\n")
        sys.stderr.flush()
        if attempt < attempts:
            time.sleep(_RETRY_DELAY_SECONDS)

    # A Python exception already recorded its own, more specific {agent}_error
    # via the agent's _record_failure(); a native crash recorded nothing.
    if returncode < 0:
        _record_failure(agent_name, run_id, f"crashed ({outcome}) on all {attempts} attempts")
    sys.stderr.write(f"[main]  {agent_name} gave up after {attempts} attempts (run_id={run_id})\n")
    sys.stderr.flush()


def _run_agent(agent_name: str, module_name: str, run_id: str, options: dict | None = None) -> None:
    """Import the agent module and call its run(run_id) function, under a
    wall-clock watchdog that hard-exits the process if it overruns."""
    timeout = _deadline_seconds()
    current = {"proc": None}  # child process, for agents in _ISOLATED_RETRIES

    def _fire() -> None:
        detail = f"{agent_name} exceeded its deadline ({timeout:.0f}s from start)"
        try:
            sys.stderr.write(f"[main]  HARD TIMEOUT — {detail}. Forcing process exit.\n")
            sys.stderr.flush()
            proc = current["proc"]
            if proc is not None:
                proc.kill()
            _record_failure(agent_name, run_id, f"hard timeout — {detail}")
        finally:
            os._exit(124)

    watchdog = threading.Timer(timeout, _fire)
    watchdog.daemon = True
    watchdog.start()
    print(f"[main]  Watchdog armed: {agent_name} must finish within {timeout:.0f}s", flush=True)

    try:
        print(f"[main]  Thread started for {module_name} (run_id={run_id})", flush=True)
        if agent_name in _ISOLATED_RETRIES:
            _run_isolated(agent_name, module_name, run_id, _ISOLATED_RETRIES[agent_name], current)
            return
        import importlib
        module = importlib.import_module(module_name)
        print(f"[main]  Module imported successfully", flush=True)
        module.run(run_id, **(options or {}))
        print(f"[main]  {module_name} completed successfully", flush=True)
    except Exception:
        sys.stderr.write(f"[main]  ERROR in {module_name} (run_id={run_id}):\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
    finally:
        watchdog.cancel()


@app.post("/")
async def trigger(request: Request):
    agent_name = os.environ.get("AGENT_NAME", "").strip()

    if not agent_name:
        return Response(
            content="AGENT_NAME environment variable is not set.",
            status_code=500,
        )

    module_name = AGENT_REGISTRY.get(agent_name)
    if not module_name:
        return Response(
            content=f"Unknown AGENT_NAME '{agent_name}'. Valid values: {list(AGENT_REGISTRY.keys())}",
            status_code=400,
        )

    # Parse Pub/Sub push envelope to extract run_id.
    # Falls back to a generated run_id for manual POST triggers (local testing, gcloud curl).
    run_id = None
    try:
        body = await request.json()
        encoded = body["message"]["data"]
        payload = json.loads(base64.b64decode(encoded).decode("utf-8"))
        run_id  = payload.get("run_id")
    except Exception:
        pass  # not a Pub/Sub envelope — fall through to fallback

    if not run_id:
        run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        print(f"[main]  No run_id in request body — generated: {run_id}", flush=True)

    # The health check has two scheduled variants: POST /?check=draft (Sunday) or /?check=send (Monday).
    options = None
    if agent_name == "healthcheck":
        check = request.query_params.get("check")
        if check and check not in ("draft", "send"):
            return Response(content=f"Unknown check '{check}'. Valid values: draft, send.", status_code=400)
        if check:
            options = {"check": check}

    print(f"[main]  Starting {agent_name} in background thread (run_id={run_id})...", flush=True)
    thread = threading.Thread(target=_run_agent, args=(agent_name, module_name, run_id, options), daemon=True)
    thread.start()

    return {"status": "started", "agent": agent_name, "run_id": run_id}


@app.get("/health")
def health():
    """Health check endpoint — Cloud Run uses this to verify the container is up."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)