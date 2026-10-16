# agents/run_kind.py
#
# Release vs debug pipeline runs. Every pipeline_runs doc carries `run_kind`: "release" (the official weekly
# run: published on the site, mailed by agent4, checked by the health check) or "debug" (a run made to try
# something: previewable privately, never published or mailed to subscribers).
#
# A doc with no `run_kind` (every run before this field existed) counts as release. The orchestrator
# defaults an unflagged trigger to "debug", so only the scheduled job, which passes ?kind=release, publishes.

import topics

RELEASE = "release"
DEBUG = "debug"
KINDS = (RELEASE, DEBUG)

# How many recent composed runs to look through for the one wanted. Debug runs and other topics' runs sit in
# the same collection, so look past a burst of them (only the small fields are read while scanning).
_SCAN_LIMIT = 50

# Fields every scan reads: `topic` is what lets the lookups below skip other newsletters' runs.
SCAN_FIELDS = ["run_kind", "topic"]


def is_release(doc: dict | None) -> bool:
    return (doc or {}).get("run_kind", RELEASE) != DEBUG


def pick_release_id(rows: list[tuple[str, dict]], sent_only: bool = False) -> str | None:
    """First doc id in `rows` (newest first) that is a release run; with `sent_only`, one agent4 has also sent.

    Only the AI newsletter's runs count: the public pages, agent4 and the health check follow the AI newsletter,
    and a run of any other topic must never be picked up as "the latest issue" (a doc with no `topic` is AI).
    """
    for doc_id, fields in rows:
        if topics.is_default_topic(fields) and is_release(fields) and (not sent_only or fields.get("agent4_send_summary")):
            return doc_id
    return None


def is_archived_release(doc: dict | None) -> bool:
    """True only for a composed run explicitly flagged release. Unlike is_release(), a missing flag does not count:
    the archive shows only issues someone flagged (the backfill), not every old recovery or test run."""
    d = doc or {}
    return d.get("run_kind") == RELEASE and bool(d.get("newsletter_composed")) and topics.is_default_topic(d)


def list_releases(db, collection: str, limit: int = 200) -> list[dict]:
    """Archived release issues, newest first, as [{run_id, subject, started_at}].

    Scans composed runs with the same (newsletter_composed, started_at) index as latest_run, reading only the
    small fields, and filters in code, so no extra index is needed.
    """
    from google.cloud import firestore

    scan = (
        db.collection(collection)
        .where("newsletter_composed", "==", True)
        .order_by("started_at", direction=firestore.Query.DESCENDING)
        .select(SCAN_FIELDS + ["newsletter_composed", "newsletter_subject", "started_at"])
        .limit(limit)
        .stream()
    )
    out = []
    for d in scan:
        f = d.to_dict() or {}
        if is_archived_release(f):
            started = f.get("started_at")
            out.append({
                "run_id": d.id,
                "subject": f.get("newsletter_subject") or "",
                "started_at": started.isoformat() if hasattr(started, "isoformat") else (started or ""),
            })
    return out


def latest_run(db, collection: str, release_only: bool = True, sent_only: bool = False,
               topic: str = topics.DEFAULT_TOPIC):
    """The newest composed pipeline run of one topic (the AI newsletter by default) as a Firestore snapshot, or None.

    release_only=False also returns debug runs. sent_only=True returns the newest release run that has been
    mailed (it carries agent4's send summary), so a draft still waiting for Monday's send is skipped.
    A non-AI `topic` is for the admin preview only: it needs release_only=False, because those topics have no
    release runs.
    Only the cheap fields are read while scanning (run docs can be close to 1 MiB); the chosen doc is then
    fetched in full. Uses the same (newsletter_composed, started_at) index agent4 already depends on.
    """
    from google.cloud import firestore

    scan = (
        db.collection(collection)
        .where("newsletter_composed", "==", True)
        .order_by("started_at", direction=firestore.Query.DESCENDING)
        .select(SCAN_FIELDS + ["started_at", "agent4_send_summary"])
        .limit(_SCAN_LIMIT)
        .stream()
    )
    rows = [(d.id, d.to_dict() or {}) for d in scan]
    if topic == topics.DEFAULT_TOPIC:
        if sent_only:
            doc_id = pick_release_id(rows, sent_only=True)
        else:
            doc_id = pick_release_id(rows) if release_only else next(
                (i for i, f in rows if topics.is_default_topic(f)), None)
    else:
        if release_only or sent_only:
            raise ValueError("only the AI newsletter has release runs")
        doc_id = next((i for i, f in rows if topics.topic_id_of(f) == topic), None)
    if doc_id is None:
        return None
    return db.collection(collection).document(doc_id).get()
