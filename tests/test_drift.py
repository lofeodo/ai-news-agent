from drift import summarize_audit


def _row(conf, final):
    return {"confidence": conf, "final_category": final, "first_pass_category": final}


def test_summarize_audit_histogram_and_categories():
    audit = [_row(5, "A"), _row(5, "A"), _row(3, "B"), _row(None, "B"), _row(9, "B")]
    s = summarize_audit(audit)
    assert s["confidence_hist"] == {"1": 0, "2": 0, "3": 1, "4": 0, "5": 2, "none": 2}
    assert s["category_counts"] == {"A": 2, "B": 3}


def test_summarize_audit_empty():
    s = summarize_audit([])
    assert sum(s["confidence_hist"].values()) == 0
    assert s["category_counts"] == {}
