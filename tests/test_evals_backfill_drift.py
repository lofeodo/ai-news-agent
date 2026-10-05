from evals.backfill_drift_summary import backfill
from fakes_firestore import FakeDb

AUDIT = [{"confidence": 5, "final_category": "A"}, {"confidence": 2, "final_category": "B"}]


def _store():
    return {
        "pipeline_runs": {
            "old": {"started_at": "2026-09-23T06:00:00+00:00", "agent1b_review_summary": {"mode": "graph", "articles": 2}},
            "done": {"started_at": "2026-09-30T06:00:00+00:00",
                     "agent1b_review_summary": {"mode": "graph", "confidence_hist": {}, "category_counts": {}}},
            "none": {"started_at": "2026-09-16T06:00:00+00:00"},
        },
        "agent1b_audits": {"old": {"articles": AUDIT}},
    }


def test_dry_run_writes_nothing():
    db = FakeDb(_store())
    rows = {r["run_id"]: r["action"] for r in backfill(db)}
    assert rows == {"done": "already_has_summary", "old": "would_write", "none": "skipped_no_graph_data"}
    assert "confidence_hist" not in db.store["pipeline_runs"]["old"]["agent1b_review_summary"]


def test_apply_adds_fields_and_keeps_existing_ones():
    db = FakeDb(_store())
    backfill(db, apply=True)
    s = db.store["pipeline_runs"]["old"]["agent1b_review_summary"]
    assert s["mode"] == "graph" and s["articles"] == 2
    assert s["confidence_hist"]["5"] == 1 and s["category_counts"] == {"A": 1, "B": 1}
    assert "agent1b_review_summary" not in db.store["pipeline_runs"]["none"]
