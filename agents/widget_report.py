# agents/widget_report.py
#
# The health check's "Widgets" section: for each newsletter's latest release run, the status of every widget
# (agent3 writes widgets_summary on the run doc). Informational only in v1: a failing widget is simply left out
# of that week's email, so it never marks a run as unhealthy. Never raises.

import run_kind
import topics

LABELS = {
    "ok": "ok",
    "failed": "FAILED (left out)",
    "skipped_no_key": "skipped: no key",
    "timed_out": "timed out (left out)",
    "empty": "nothing this week",
    "stale": "stale data (left out)",
}
HEADER = "Widgets (informational: a widget problem never marks a run as failed):"


def format_line(name: str, run_id: str, summary: dict) -> str:
    parts = [f"{wid} {LABELS.get(status, status)}" for wid, status in (summary or {}).items()]
    return f"  {name} (run {run_id}): " + (", ".join(parts) if parts else "no widgets")


def format_section(items: list[tuple[str, str, dict]]) -> str:
    """items: [(newsletter name, run id, widgets_summary), ...]. "" when there is nothing to report."""
    if not items:
        return ""
    return "\n".join([HEADER] + [format_line(*i) for i in items])


def _rows(db, collection: str) -> list[tuple[str, dict]]:
    from google.cloud import firestore
    query = (db.collection(collection).order_by("started_at", direction=firestore.Query.DESCENDING)
             .select(run_kind.SCAN_FIELDS + ["started_at", "widgets_summary"]).limit(run_kind.SCAN_LIMIT))
    return [(d.id, d.to_dict() or {}) for d in query.stream()]


def section(db, collection: str) -> str:
    """The section text, starting with a blank line, or "" when no recent release run has widgets."""
    try:
        rows = _rows(db, collection)
        by_id = dict(rows)
        items = []
        ordered = sorted((topics.get_topic(t) for t in topics.list_topic_ids()),
                         key=lambda t: (not t.is_default, t.name.lower()))
        for topic in ordered:
            run_id = run_kind.pick_release_id(rows, topic=topic.id)
            summary = (by_id.get(run_id) or {}).get("widgets_summary") if run_id else None
            if summary:
                items.append((topic.name, run_id, summary))
        text = format_section(items)
        return "\n\n" + text if text else ""
    except Exception as e:
        print(f"[healthcheck]  widget report failed (ignored): {e}", flush=True)
        return f"\n\nWidgets: the report failed ({type(e).__name__})."
