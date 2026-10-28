"""Archive a pipeline run's LangSmith token usage and cost onto its Firestore run doc.

LangSmith's free plan keeps traces for 14 days, but drift needs several weeks of history, so the
weekly healthcheck copies each run's totals into `pipeline_runs/{run_id}.llm_usage` (a few hundred
bytes) before they expire. Calls are found by the tags agents/tracing.py stamps on every traced
Claude call: `run:<run_id>` and `agent:<name>`.

Token counts and `total_cost` are LangSmith's own numbers (its cost is an estimate from its model
price list). A call LangSmith cannot price (no `total_cost`; DeepSeek models since 2026-10-28) is priced
here from agents/pricing.py by the model name LangSmith recorded, and counted in `calls_priced_locally`;
`calls_without_cost` is left for calls neither can price. The repo does not use prompt caching, so cache
tokens are not tracked separately.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import FIRESTORE_COLLECTION
import pricing

USAGE_FIELD = "llm_usage"
_SELECT = ["tags", "prompt_tokens", "completion_tokens", "total_cost", "extra"]


def _model_of(run) -> str | None:
    """Model name as LangSmith's Anthropic wrapper records it (extra.metadata.ls_model_name)."""
    extra = getattr(run, "extra", None) or {}
    return (extra.get("metadata") or {}).get("ls_model_name") if isinstance(extra, dict) else None


def _local_cost(run) -> float | None:
    model = _model_of(run)
    if not model:
        return None
    try:
        return pricing.estimate_cost(getattr(run, "prompt_tokens", None) or 0,
                                     getattr(run, "completion_tokens", None) or 0, model)
    except KeyError:
        return None


def _list_llm_runs(ls_client, run_id: str):
    kwargs = dict(
        project_name=os.environ.get("LANGSMITH_PROJECT") or None,
        run_type="llm",
        filter=f'has(tags, "run:{run_id}")',
    )
    try:
        return list(ls_client.list_runs(select=_SELECT, **kwargs))
    except Exception:
        # A field name LangSmith rejects in `select` must not lose the whole archive.
        return list(ls_client.list_runs(**kwargs))


def _agent_of(tags) -> str:
    for t in tags or []:
        if t.startswith("agent:"):
            return t.split(":", 1)[1]
    return "unknown"


def fetch_run_usage(ls_client, run_id: str) -> dict | None:
    """Per-agent and total usage for one run from LangSmith, or None if no traced calls were found."""
    agents: dict[str, dict] = {}
    for r in _list_llm_runs(ls_client, run_id):
        a = agents.setdefault(_agent_of(getattr(r, "tags", None)),
                              {"input": 0, "output": 0, "calls": 0, "cost": 0.0, "calls_without_cost": 0,
                               "calls_priced_locally": 0})
        a["input"] += getattr(r, "prompt_tokens", None) or 0
        a["output"] += getattr(r, "completion_tokens", None) or 0
        a["calls"] += 1
        cost = getattr(r, "total_cost", None)
        if cost is None:
            cost = _local_cost(r)
            if cost is None:
                a["calls_without_cost"] += 1
            else:
                a["calls_priced_locally"] += 1
                a["cost"] += cost
        else:
            a["cost"] += float(cost)
    if not agents:
        return None
    total = {k: sum(a[k] for a in agents.values())
             for k in ("input", "output", "calls", "cost", "calls_without_cost", "calls_priced_locally")}
    return {"source": "langsmith", "archived_at": datetime.now(timezone.utc).isoformat(),
            "agents": agents, "total": total}


def archive_run(db, ls_client, run_id: str, doc: dict | None = None) -> dict | None:
    """Ensure `llm_usage` is on the run doc. Idempotent; returns the usage or None if unavailable.

    Nothing is written when LangSmith has no calls for the run, so a later run can retry it
    while the traces are still inside LangSmith's retention window.
    """
    ref = db.collection(FIRESTORE_COLLECTION).document(run_id)
    if doc is None:
        snap = ref.get()
        doc = (snap.to_dict() or {}) if snap.exists else {}
    if doc.get(USAGE_FIELD):
        return doc[USAGE_FIELD]
    usage = fetch_run_usage(ls_client, run_id)
    if usage is None:
        return None
    ref.set({USAGE_FIELD: usage}, merge=True)
    return usage


def catch_up(db, ls_client, run_ids_and_docs) -> dict[str, dict | None]:
    """archive_run() over several (run_id, doc) pairs; one failure does not stop the others."""
    out = {}
    for run_id, doc in run_ids_and_docs:
        try:
            out[run_id] = archive_run(db, ls_client, run_id, doc)
        except Exception as e:
            print(f"[usage_archive]  could not archive {run_id}: {e}", flush=True)
            out[run_id] = None
    return out
