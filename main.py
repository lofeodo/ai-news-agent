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
from datetime import datetime, timezone

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
        allow_headers=["Content-Type"],
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(subscriptions_router)


def _run_agent(module_name: str, run_id: str) -> None:
    """Import the agent module and call its run(run_id) function."""
    try:
        print(f"[main]  Thread started for {module_name} (run_id={run_id})", flush=True)
        import importlib
        module = importlib.import_module(module_name)
        print(f"[main]  Module imported successfully", flush=True)
        module.run(run_id)
        print(f"[main]  {module_name} completed successfully", flush=True)
    except Exception:
        sys.stderr.write(f"[main]  ERROR in {module_name} (run_id={run_id}):\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()


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

    print(f"[main]  Starting {agent_name} in background thread (run_id={run_id})...", flush=True)
    thread = threading.Thread(target=_run_agent, args=(module_name, run_id), daemon=True)
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