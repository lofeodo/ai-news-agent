from evals.run_drift_null_sim import simulate


def _rows(n=200):
    cats = ["A", "B", "C", "D"]
    return [{"confidence": 3 + i % 3, "final_category": cats[i % 4], "routed_to_review": i % 4 == 0} for i in range(n)]


def test_simulation_is_seeded_and_counts_every_week():
    a, b = simulate(_rows(), 10, seed=5), simulate(_rows(), 10, seed=5)
    assert a == b and a["any"] <= 10


def test_large_planted_shift_is_detected_every_time():
    out = simulate(_rows(), 10, seed=1, shift_to="A", shift_frac=0.5)
    assert out["category_mix"] == 10
