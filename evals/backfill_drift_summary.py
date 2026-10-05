"""Backfill `confidence_hist` and `category_counts` onto older runs' `agent1b_review_summary`.

Older graph-mode runs only have the per-article `agent1b_audits/{run_id}` doc; this computes
the same summary agent1b now writes itself. Additive merge write, no LLM calls, cost 0 USD.
Dry run by default: nothing is written without --apply.

CMD:  set GCP_PROJECT_ID=<project>
      venv\Scripts\python -m evals.backfill_drift_summary            (dry run)
      venv\Scripts\python -m evals.backfill_drift_summary --apply
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
from drift_history import run_summary  # noqa: E402

COLLECTION = "pipeline_runs"


def backfill(db, apply: bool = False, limit: int = 50) -> list[dict]:
    """Return one action row per run examined; writes only when `apply` is true."""
    from google.cloud import firestore

    query = db.collection(COLLECTION).order_by("started_at", direction=firestore.Query.DESCENDING).limit(limit)
    actions = []
    for d in query.stream():
        doc = d.to_dict() or {}
        review = doc.get("agent1b_review_summary") or {}
        if "confidence_hist" in review:
            actions.append({"run_id": d.id, "action": "already_has_summary"})
            continue
        summary = run_summary(db, d.id, doc)
        if summary is None:
            actions.append({"run_id": d.id, "action": "skipped_no_graph_data"})
            continue
        if apply:
            merged = {**review, "confidence_hist": summary["confidence_hist"],
                      "category_counts": summary["category_counts"]}
            db.collection(COLLECTION).document(d.id).set({"agent1b_review_summary": merged}, merge=True)
        actions.append({"run_id": d.id, "action": "written" if apply else "would_write"})
    return actions


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write to Firestore (default: dry run)")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--project", default=os.environ.get("GCP_PROJECT_ID"))
    args = ap.parse_args(argv)
    if not args.project:
        sys.exit("Set GCP_PROJECT_ID or pass --project.")
    from google.cloud import firestore

    for row in backfill(firestore.Client(project=args.project), apply=args.apply, limit=args.limit):
        print(f"{row['run_id']}: {row['action']}")
    if not args.apply:
        print("Dry run: nothing written. Re-run with --apply.")


if __name__ == "__main__":
    main()
