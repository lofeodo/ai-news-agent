import numpy as np

from drift import category_mix_test, confidence_ks, evaluate, review_rate_test, summarize_audit


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


KW = dict(min_runs=3, p_threshold=0.01, min_ks_d=0.15, min_share_shift=0.10, min_rate_shift=0.10)
CATS = ["A", "B", "C", "D", "E", "F", "G"]
CAT_P = [0.25, 0.2, 0.15, 0.15, 0.1, 0.1, 0.05]
CONF_P = [0.02, 0.08, 0.2, 0.4, 0.3]  # confidence 1..5


def _week(rng, n=500, cat_p=CAT_P, conf_p=CONF_P, review_p=0.3):
    conf = np.bincount(rng.choice(5, n, p=conf_p), minlength=5)
    cats = np.bincount(rng.choice(len(CATS), n, p=cat_p), minlength=len(CATS))
    return {"confidence_hist": {**{str(i + 1): int(c) for i, c in enumerate(conf)}, "none": 0},
            "category_counts": {c: int(k) for c, k in zip(CATS, cats)},
            "articles": n, "routed_to_review": int(rng.binomial(n, review_p))}


def test_null_weeks_rarely_flag():
    rng = np.random.default_rng(1)
    flags = sum(evaluate(_week(rng), [_week(rng) for _ in range(4)], **KW)["status"] == "drift"
                for _ in range(200))
    assert flags <= 4  # observed rate well under 2%; the double gate (p and effect size) is conservative


def test_planted_shifts_are_detected():
    rng = np.random.default_rng(2)
    base = [_week(rng) for _ in range(4)]
    low_conf = _week(rng, conf_p=[0.2, 0.3, 0.3, 0.15, 0.05])
    mix = _week(rng, cat_p=[0.05, 0.05, 0.1, 0.1, 0.1, 0.1, 0.5])
    review = _week(rng, review_p=0.6)
    for week, metric in [(low_conf, "confidence"), (mix, "category_mix"), (review, "review_rate")]:
        out = evaluate(week, base, **KW)
        assert out["status"] == "drift"
        assert [r["metric"] for r in out["results"] if r["status"] == "drift"].count(metric) == 1


def test_insufficient_history():
    rng = np.random.default_rng(3)
    out = evaluate(_week(rng), [_week(rng), _week(rng)], **KW)
    assert out == {"status": "insufficient_history", "baseline_runs": 2, "results": []}


def test_tiny_and_empty_inputs_do_not_raise():
    assert confidence_ks({}, {}, 0.01, 0.15)["status"] == "insufficient_data"
    assert category_mix_test({}, {"A": 3}, 0.01, 0.1)["status"] == "insufficient_data"
    assert category_mix_test({"A": 3}, {"A": 5}, 0.01, 0.1)["status"] == "insufficient_data"  # one category
    assert review_rate_test(0, 0, 3, 10, 0.01, 0.1)["status"] == "insufficient_data"


def test_rare_categories_use_permutation_and_are_seeded():
    cur, base = {"A": 20, "B": 2, "C": 1}, {"A": 90, "B": 8, "C": 2}
    a, b = category_mix_test(cur, base, 0.01, 0.1), category_mix_test(cur, base, 0.01, 0.1)
    assert a["method"] == "permutation" and a["p_value"] == b["p_value"]
    assert a["status"] == "ok"


def test_effect_floor_blocks_significant_but_small_shift():
    # Significant at huge n, but only a 3-point shift: must not flag.
    r = review_rate_test(3300, 10000, 3000, 10000, 0.01, 0.10)
    assert r["p_value"] < 0.01 and r["status"] == "ok"
