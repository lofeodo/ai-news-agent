from drift_history import load_baseline, run_summary
from fakes_firestore import FakeDb

NEW = {"confidence_hist": {"1": 0, "2": 0, "3": 1, "4": 2, "5": 3, "none": 0},
       "category_counts": {"A": 4, "B": 2}, "articles": 6, "routed_to_review": 1}


def _run(day, summary=None):
    doc = {"started_at": f"2026-09-{day:02d}T06:00:00+00:00"}
    if summary is not None:
        doc["agent1b_review_summary"] = summary
    return doc


def test_new_style_summary_read_from_run_doc():
    s = run_summary(FakeDb(), "r1", _run(1, NEW))
    assert s["confidence_hist"]["5"] == 3 and s["articles"] == 6 and s["routed_to_review"] == 1


def test_old_run_falls_back_to_audit_doc():
    audit = [{"confidence": 4, "final_category": "A"}, {"confidence": None, "final_category": "B"}]
    db = FakeDb({"agent1b_audits": {"r1": {"articles": audit}}})
    s = run_summary(db, "r1", _run(1, {"mode": "graph", "articles": 2, "routed_to_review": 0}))
    assert s["confidence_hist"]["4"] == 1 and s["confidence_hist"]["none"] == 1
    assert s["category_counts"] == {"A": 1, "B": 1}


def test_runs_without_graph_data_are_skipped():
    assert run_summary(FakeDb(), "r1", _run(1)) is None  # no summary at all (single_pass / failed early)
    assert run_summary(FakeDb(), "r1", _run(1, {"mode": "graph", "articles": 2})) is None  # audit doc missing


def test_load_baseline_excludes_current_skips_unusable_and_caps():
    store = {"pipeline_runs": {
        "cur": _run(30, NEW), "w4": _run(23, NEW), "w3": _run(16),  # w3 unusable
        "w2": _run(9, NEW), "w1": _run(2, NEW), "w0": _run(1, NEW),
    }}
    got = load_baseline(FakeDb(store), "cur", 3)
    assert [r["run_id"] for r in got] == ["w4", "w2", "w1"]
