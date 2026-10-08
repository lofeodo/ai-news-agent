import xml.dom.minidom

import pytest

from evals import make_charts as c


def _m(value, n=90, lo=0.4, hi=0.8, k=None):
    d = {"value": value, "n": n, "ci_low": lo, "ci_high": hi}
    if k is not None:
        d["k"] = k
    return d


def _review(overlap=True):
    hi = 0.8 if overlap else 0.5
    return {"metrics": {"accuracy_single_pass": _m(0.62, hi=0.8), "accuracy_graph_first_pass": _m(0.59, lo=0.45, hi=hi),
                        "accuracy_graph_final": _m(0.9 if not overlap else 0.6, lo=0.7 if not overlap else 0.4, hi=0.95 if not overlap else 0.8)}}


def _inj(success, after=False):
    m = {"attack_success__overall": _m(success / 10, 10, 0.0, 0.5, k=success),
         "control_success__overall": _m(0, 10, 0.0, 0.3, k=0),
         "attack_success__attack__ssrf_steer": _m(success / 10, 10, 0.0, 0.76, k=success),
         "attack_success__attack__quiet": _m(0, 10, 0.0, 0.2, k=0)}
    return {"metrics": m}


def _parses(svg):
    return xml.dom.minidom.parseString(svg)


def test_review_chart_is_valid_svg_with_every_number():
    svg = c.review_accuracy_svg(_review())
    _parses(svg)
    for needle in ("62%", "59%", "60%", "n=90", "Single-pass", "overlap"):
        assert needle in svg


def test_review_chart_notes_a_non_overlap():
    assert "do not overlap" in c.review_accuracy_svg(_review(overlap=False))


def test_review_chart_requires_metrics():
    with pytest.raises(ValueError):
        c.review_accuracy_svg({"metrics": {}})


def test_injection_chart_lists_only_scopes_that_ever_succeeded():
    svg = c.injection_svg(_inj(5), _inj(0))
    _parses(svg)
    assert "ssrf steer" in svg and "quiet" not in svg
    assert "5/10" in svg and "0/10" in svg


def test_kappa_chart_shows_value_interval_and_bar():
    doc = {"metrics": {"cohens_kappa": {"value": 0.04, "n": 40, "ci_low": -0.15, "ci_high": 0.23}}}
    svg = c.judge_kappa_svg(doc)
    _parses(svg)
    assert "kappa 0.04 (-0.15 to 0.23)" in svg and f"{c.KAPPA_BAR:.1f}" in svg


def test_write_all_skips_missing_results_and_writes_the_rest(tmp_path):
    out = tmp_path / "assets"
    assert c.write_all(tmp_path / "none", out) == []
    from evals import results
    doc = results.build_results("judge_calibration", "m", {"cohens_kappa": {"value": 0.1, "n": 5, "ci_low": 0.0, "ci_high": 0.2}},
                                cost_usd=0.0, notes="")
    results.write_results(doc, tmp_path)
    written = c.write_all(tmp_path, out)
    assert [p.name for p in written] == ["chart-judge-kappa.svg"]
