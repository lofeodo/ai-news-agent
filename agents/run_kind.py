# agents/run_kind.py
#
# Release vs debug pipeline runs. Every pipeline_runs doc carries `run_kind`: "release" (the official weekly
# run: published on the site, mailed by agent4, checked by the health check) or "debug" (a run made to try
# something: previewable privately, never published or mailed to subscribers).
#
# A doc with no `run_kind` (every run before this field existed) counts as release. The orchestrator
# defaults an unflagged trigger to "debug", so only the scheduled job, which passes ?kind=release, publishes.

RELEASE = "release"
DEBUG = "debug"
KINDS = (RELEASE, DEBUG)

# How many recent composed runs to look through for a release one. A debug run is rare, so a handful is plenty.
_SCAN_LIMIT = 10


def is_release(doc: dict | None) -> bool:
    return (doc or {}).get("run_kind", RELEASE) != DEBUG


def pick_release_id(rows: list[tuple[str, dict]]) -> str | None:
    """First doc id in `rows` (newest first) that is a release run."""
    for doc_id, fields in rows:
        if is_release(fields):
            return doc_id
    return None


def latest_run(db, collection: str, release_only: bool = True):
    """The newest composed pipeline run as a Firestore snapshot, or None.

    release_only=False also returns debug runs. Only the cheap fields are read while scanning (run docs can be
    close to 1 MiB); the chosen doc is then fetched in full. Uses the same (newsletter_composed, started_at)
    index agent4 already depends on.
    """
    from google.cloud import firestore

    scan = (
        db.collection(collection)
        .where("newsletter_composed", "==", True)
        .order_by("started_at", direction=firestore.Query.DESCENDING)
        .select(["run_kind", "started_at"])
        .limit(_SCAN_LIMIT if release_only else 1)
        .stream()
    )
    rows = [(d.id, d.to_dict() or {}) for d in scan]
    doc_id = pick_release_id(rows) if release_only else (rows[0][0] if rows else None)
    if doc_id is None:
        return None
    return db.collection(collection).document(doc_id).get()
