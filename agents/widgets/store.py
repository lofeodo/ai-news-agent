# agents/widgets/store.py
#
# Where widget data lives (contract C4):
#   edition widgets   pipeline_runs/{run_id}/widgets/{widget_id} = {data, produced_at, status[, error]}, plus
#                     `widgets_summary` (statuses only) on the run doc; local mode: data/widgets/<widget_id>.json
#   personal widgets  widget_cache/{widget_id}__{config_hash}__{week} = {data, produced_at, expires_at, ...}
#                     (Firestore TTL on expires_at), so readers with the same config share one fetch a week.

import json
import os
from datetime import datetime, timedelta, timezone

from .config import config_hash, validate
from .runner import OK, RunResult, produce_one

SUBCOLLECTION = "widgets"
SUMMARY_FIELD = "widgets_summary"
CACHE_COLLECTION = "widget_cache"
CACHE_DAYS = 8


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_edition(db, run_id: str, result: RunResult, collection: str = "pipeline_runs",
                 now: datetime | None = None) -> dict:
    """Write each widget's doc and the run doc's widgets_summary. Returns the summary."""
    run_ref = db.collection(collection).document(run_id)
    produced_at = _now_iso(now)
    for wid, status in result.status.items():
        doc = {"data": result.data.get(wid), "produced_at": produced_at, "status": status["status"]}
        if status.get("error"):
            doc["error"] = status["error"][:300]
        run_ref.collection(SUBCOLLECTION).document(wid).set(doc)
    summary = result.summary()
    run_ref.set({SUMMARY_FIELD: summary}, merge=True)
    return summary


def load_edition(db, run_id: str, collection: str = "pipeline_runs") -> dict:
    """{widget id: data} for every widget of a run that produced fine."""
    out = {}
    for snap in db.collection(collection).document(run_id).collection(SUBCOLLECTION).stream():
        d = snap.to_dict() or {}
        if d.get("status") == OK and d.get("data") is not None:
            out[snap.id] = d["data"]
    return out


def save_local(data_dir: str, result: RunResult, now: datetime | None = None) -> None:
    folder = os.path.join(data_dir, "widgets")
    os.makedirs(folder, exist_ok=True)
    produced_at = _now_iso(now)
    for wid, status in result.status.items():
        with open(os.path.join(folder, f"{wid}.json"), "w", encoding="utf-8") as f:
            json.dump({"data": result.data.get(wid), "produced_at": produced_at, **status}, f,
                      indent=2, ensure_ascii=False)
    with open(os.path.join(folder, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(result.summary(), f, indent=2)


def load_local(data_dir: str) -> dict:
    folder = os.path.join(data_dir, "widgets")
    out = {}
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json") or name == "summary.json":
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            d = json.load(f)
        if d.get("status") == OK and d.get("data") is not None:
            out[name[:-5]] = d["data"]
    return out


def cache_key(widget_id: str, config: dict, week: str) -> str:
    return f"{widget_id}__{config_hash(config)}__{week}"


def get_or_produce_personal(db, widget, config: dict, week: str, ctx=None, now: datetime | None = None):
    """A personal widget's data for one config and week: from the cache, or produced once and cached.
    Returns None when the config is invalid or the widget produces nothing (the block is then left out).
    Roadmap E calls this from the studio preview and the custom-newsletter send."""
    from .base import ProduceContext
    from .config import ConfigError
    try:
        config = validate(widget.config_fields, config)
    except ConfigError:
        return None
    now = now or datetime.now(timezone.utc)
    ref = db.collection(CACHE_COLLECTION).document(cache_key(widget.id, config, week))
    snap = ref.get()
    if snap.exists:
        cached = snap.to_dict() or {}
        expires = cached.get("expires_at")
        if cached.get("data") is not None and (expires is None or expires > now):
            return cached["data"]
    if widget.missing_keys():
        return None
    ctx = ctx or ProduceContext(week=week, now=now)
    status, data, _ = produce_one(widget, ctx, config)
    if status != OK:
        return None
    ref.set({"widget": widget.id, "config_hash": config_hash(config), "week": week, "data": data,
             "produced_at": _now_iso(now), "expires_at": now + timedelta(days=CACHE_DAYS)})
    return data
