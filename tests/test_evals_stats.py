import pytest

from evals.stats import cohens_kappa, paired_wins_losses, rate_with_ci, wilson_interval


def test_wilson_known_values():
    low, high = wilson_interval(5, 10)
    assert low == pytest.approx(0.2366, abs=1e-3)
    assert high == pytest.approx(0.7634, abs=1e-3)


def test_wilson_extremes_stay_in_bounds():
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and 0.2 < high < 0.35
    low, high = wilson_interval(10, 10)
    assert high == pytest.approx(1.0) and 0.65 < low < 0.8


def test_wilson_empty_sample_is_uninformative():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_rate_with_ci_reports_n():
    r = rate_with_ci(82, 100)
    assert r["n"] == 100 and r["value"] == 0.82
    assert r["ci_low"] < 0.82 < r["ci_high"]
    assert rate_with_ci(0, 0)["value"] is None


def test_kappa_perfect_and_chance():
    assert cohens_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == pytest.approx(1.0)
    assert cohens_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == pytest.approx(0.0)


def test_kappa_known_value():
    a = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    b = ["y"] * 20 + ["y"] * 5 + ["n"] * 10 + ["n"] * 15
    assert cohens_kappa(a, b) == pytest.approx(0.4, abs=1e-6)


def test_kappa_rejects_bad_input():
    with pytest.raises(ValueError):
        cohens_kappa(["a"], ["a", "b"])
    with pytest.raises(ValueError):
        cohens_kappa([], [])


def test_paired_wins_losses():
    a = [True, True, False, False, True]
    b = [True, False, True, False, True]
    assert paired_wins_losses(a, b) == {"wins": 1, "losses": 1, "ties": 3, "n": 5}
    with pytest.raises(ValueError):
        paired_wins_losses([True], [True, False])
