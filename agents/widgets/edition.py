# agents/widgets/edition.py
#
# agent3's one entry point: produce a topic run's edition widgets (the profile's `widgets:` list), store them
# (Firestore pipeline_runs/{run_id}/widgets/* + widgets_summary, or data/widgets/ locally) and return the data
# for the renderer. Never raises: a widget problem must never fail a run.

from .base import ProduceContext
from .runner import FAILED, RunResult, produce_edition_widgets
from . import store


def produce_for_run(topic, run_id: str, *, week: str, picks: dict | None = None, run_doc: dict | None = None,
                    client=None, db=None, data_dir: str | None = None,
                    collection: str = "pipeline_runs") -> RunResult:
    entries = list(getattr(topic, "widgets", ()) or ())
    if not entries:
        return RunResult()
    try:
        ctx = ProduceContext(topic=topic.id, run_id=run_id, week=week, picks=picks or {}, run_doc=run_doc or {},
                             client=client)
        result = produce_edition_widgets(ctx, entries)
        for u in ctx.usage:
            print(f"  [widgets] {u['widget']}: {u['input_tokens']} in / {u['output_tokens']} out tokens", flush=True)
    except Exception as e:
        print(f"  [widgets] could not produce widgets: {type(e).__name__}: {e}", flush=True)
        result = RunResult(status={w.id: {"status": FAILED, "error": f"{type(e).__name__}"} for w in entries})
    for wid, s in result.status.items():
        print(f"  [widgets] {wid}: {s['status']}" + (f" ({s['error']})" if s.get("error") else ""), flush=True)
    try:
        if db is not None:
            store.save_edition(db, run_id, result, collection=collection)
        elif data_dir:
            store.save_local(data_dir, result)
    except Exception as e:
        print(f"  [widgets] could not store widget data: {type(e).__name__}: {e}", flush=True)
    return result
