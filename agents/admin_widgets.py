# agents/admin_widgets.py
#
# GET /auth/admin/widgets: the admin widget gallery's data (public/newsletter/widgets.html). Admin only, like the
# debug preview. For every registered widget: its metadata, which profiles place it, its sample render and, when
# a run has produced it, its latest real render. Each render is a whole email page holding just that widget, so
# the gallery shows it at email width with the email's own phone styles. Mounted in main.py for
# agent-subscriptions; a router of its own so the subscription module stays untouched.

from fastapi import APIRouter, Depends, HTTPException, Request

import render
import topics
from agent_subscriptions import FIRESTORE_COLLECTION, _db, _is_admin, limiter
from auth_middleware import get_verified_user
from render import shell
from render.text import esc
from widgets import registry
from widgets.store import SUBCOLLECTION

router = APIRouter()
SCAN_RUNS = 60


def _topic_for(widget) -> str:
    return next((t for t in widget.topics if t != "*" and topics.is_valid_topic(t)), topics.DEFAULT_TOPIC)


def _page(widget, data, topic_id: str) -> str:
    """A full email document holding only this widget's fragment ("" when it renders nothing)."""
    topic = topics.get_topic(topic_id if topics.is_valid_topic(topic_id) else _topic_for(widget))
    look = render.Look.from_topic(topic, week_of="")
    try:
        fragment = widget.render(data, {}, look) or ""
    except Exception as e:
        return f"<p>render failed: {esc(type(e).__name__)}</p>"
    return shell.page(esc(widget.name), "", fragment) if fragment else ""


def _placements() -> dict:
    """{widget id: [{"topic", "placement"}, ...]} from the topic profiles."""
    out: dict = {}
    for tid in topics.list_topic_ids():
        for w in topics.get_topic(tid).widgets:
            out.setdefault(w.id, []).append({"topic": tid, "placement": w.placement})
    return out


def _latest(db) -> tuple[dict, dict]:
    """({widget id: latest produced render info}, {widget id: its newest status, any outcome}), from the newest
    runs' widgets_summary."""
    from google.cloud import firestore
    rows = (db.collection(FIRESTORE_COLLECTION).order_by("started_at", direction=firestore.Query.DESCENDING)
            .select(["topic", "run_kind", "started_at", "widgets_summary"]).limit(SCAN_RUNS).stream())
    out, statuses = {}, {}
    for snap in rows:
        fields = snap.to_dict() or {}
        for wid, status in (fields.get("widgets_summary") or {}).items():
            statuses.setdefault(wid, {"status": status, "run_id": snap.id})
            if status != "ok" or wid in out or registry.get(wid) is None:
                continue
            doc = (db.collection(FIRESTORE_COLLECTION).document(snap.id).collection(SUBCOLLECTION)
                   .document(wid).get().to_dict() or {})
            if doc.get("data") is None:
                continue
            topic_id = topics.topic_id_of(fields)
            out[wid] = {"run_id": snap.id, "run_kind": fields.get("run_kind", "release"), "topic": topic_id,
                        "produced_at": doc.get("produced_at"),
                        "html": _page(registry.get(wid), doc["data"], topic_id)}
    return out, statuses


@router.get("/auth/admin/widgets")
@limiter.limit("10/minute")
def admin_widgets(request: Request, user: dict = Depends(get_verified_user)):
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="forbidden")
    try:
        latest, statuses = _latest(_db())
    except Exception as e:  # the gallery still shows every sample
        print(f"[admin_widgets]  latest renders unavailable: {type(e).__name__}: {e}", flush=True)
        latest, statuses = {}, {}
    placements = _placements()
    out = []
    for w in registry.all_widgets():
        out.append({**w.info(), "kind": w.kind, "commercial_use": w.commercial_use, "keys": list(w.keys),
                    "last_status": statuses.get(w.id), "placed_in": placements.get(w.id, []),
                    "sample_html": _page(w, w.sample_data(), _topic_for(w)), "latest": latest.get(w.id)})
    return {"widgets": out}
