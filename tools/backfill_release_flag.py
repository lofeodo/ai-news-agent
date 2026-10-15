# tools/backfill_release_flag.py
#
# One-off: flag the old Monday-morning newsletters as release runs so the archive can list them.
#
# Before the move to a Sunday draft, the weekly issue was generated Monday ~10:00 UTC. Those pipeline_runs docs
# predate `run_kind`, so they have no flag. The archive lists only explicit `run_kind == "release"` docs, so this
# sets exactly that field (plus `run_kind_backfilled_at`) on the composed, unflagged, Monday-morning runs and on
# the extra ids in EXTRA_IDS. Nothing else on a doc is touched and nothing is ever set to "debug".
#
#   python tools/backfill_release_flag.py            # dry run: prints what would change
#   python tools/backfill_release_flag.py --apply    # writes it
#
# Needs Application Default Credentials for the project (gcloud auth application-default login).
import argparse
from datetime import datetime, timezone

COLLECTION = "pipeline_runs"
MONDAY = 0
MORNING_BEFORE_UTC_HOUR = 12

# 2026-09-28: the 10:00 run failed (1 MiB document cap) and two recovery runs followed; the issue that
# actually shipped was the 18:37Z run (agent4 sent at 20:45Z). Not a Monday-morning run, so listed here.
EXTRA_IDS = ["2026-09-28T183736Z"]


def _started_at(value) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # naive values are UTC (see agent_healthcheck)


def select_ids(rows: list[tuple[str, dict]]) -> list[str]:
    """Run ids to flag from (doc_id, fields) rows: composed, unflagged, started Monday morning UTC, plus EXTRA_IDS."""
    chosen = []
    by_id = dict(rows)
    for doc_id, f in rows:
        if f.get("run_kind") or not f.get("newsletter_composed"):
            continue
        dt = _started_at(f.get("started_at"))
        if dt and dt.weekday() == MONDAY and dt.hour < MORNING_BEFORE_UTC_HOUR:
            chosen.append(doc_id)
    for doc_id in EXTRA_IDS:
        f = by_id.get(doc_id)
        if f and not f.get("run_kind") and f.get("newsletter_composed") and doc_id not in chosen:
            chosen.append(doc_id)
    return sorted(chosen)


def main() -> int:
    ap = argparse.ArgumentParser(description="Flag old Monday-morning newsletters as release runs.")
    ap.add_argument("--apply", action="store_true", help="write the flag (default is a dry run)")
    args = ap.parse_args()

    from google.cloud import firestore

    db = firestore.Client()
    rows = [
        (d.id, d.to_dict() or {})
        for d in db.collection(COLLECTION).select(["run_kind", "started_at", "newsletter_composed"]).stream()
    ]
    ids = select_ids(rows)
    for i in ids:
        print(("flag        " if args.apply else "would flag  ") + i)
    print(f"{len(ids)} run(s) {'flagged' if args.apply else 'would be flagged (dry run; pass --apply)'}")
    if args.apply:
        now = datetime.now(timezone.utc).isoformat()
        for i in ids:
            db.collection(COLLECTION).document(i).update({"run_kind": "release", "run_kind_backfilled_at": now})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
