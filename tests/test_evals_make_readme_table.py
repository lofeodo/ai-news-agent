import pytest

from evals import make_readme_table as t, results, review_eval
from evals.make_readme_table import END, START


def _doc():
    rows = [{"id": "1", "stratum": "low_confidence", "gold": "A", "single_pass": "B", "first_pass": "B",
             "confidence": 2, "final": "A", "routed": True, "tool_calls": 1, "input_tokens": 5, "output_tokens": 1,
             "review_status": "reviewed"},
            {"id": "2", "stratum": "high_confidence", "gold": "A", "single_pass": "A", "first_pass": "A",
             "confidence": 5, "final": "A", "routed": False, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0,
             "review_status": "skipped"}]
    metrics, verdict = review_eval.score(rows, 4)
    return results.build_results("review_eval_2026-10-05", "m", metrics, cost_usd=0.5, notes=verdict)


def test_render_contains_every_variant_and_paired_counts():
    out = t.render(_doc())
    for label in ("Single-pass", "Graph, first pass", "Graph, after review"):
        assert f"| {label} |" in out
    assert "1 wins, 0 losses, 1 ties out of 2" in out
    assert "Routed to review" in out and "evals/results/review_eval_2026-10-05.json" in out


def test_update_readme_replaces_only_the_marked_block(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(f"before\n{START}\nSTALE-TABLE\n{END}\nafter\n", encoding="utf-8")
    t.update_readme(_doc(), readme)
    text = readme.read_text(encoding="utf-8")
    assert text.startswith("before\n") and text.endswith("after\n") and "STALE-TABLE" not in text
    t.update_readme(_doc(), readme)  # idempotent
    assert readme.read_text(encoding="utf-8") == text


def test_update_readme_requires_markers(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("no markers", encoding="utf-8")
    with pytest.raises(ValueError):
        t.update_readme(_doc(), readme)


def test_latest_results_path_ignores_rows_files(tmp_path):
    for name in ("review_eval_2026-10-01.json", "review_eval_2026-10-05.json", "review_eval_2026-10-05_rows.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    assert t.latest_results_path(tmp_path).name == "review_eval_2026-10-05.json"
    assert t.latest_results_path(tmp_path / "missing") is None




def _inj_doc(name, ok_rate, attempted=None):
    m = {"attack_success__overall": {"k": ok_rate, "n": 10, "value": ok_rate / 10, "ci_low": 0.0, "ci_high": 0.5},
         "control_success__overall": {"k": 0, "n": 10, "value": 0, "ci_low": 0.0, "ci_high": 0.3},
         "attack_success__agent__categorize": {"k": ok_rate, "n": 10, "value": 0, "ci_low": 0.0, "ci_high": 0.5},
         "attack_success__attack__ssrf_steer": {"k": ok_rate, "n": 10, "value": 0, "ci_low": 0.0, "ci_high": 0.5}}
    if attempted is not None:
        m["fetch_attempted__attack"] = {"k": attempted, "n": 10, "value": 0, "ci_low": 0.0, "ci_high": 0.5}
    return results.build_results(name, "m", m, cost_usd=0.3, notes="limits stated here")


def test_render_injection_compares_before_and_after():
    out = t.render_injection(_inj_doc("injection_eval_baseline", 5), _inj_doc("injection_eval_after", 0, attempted=5))
    assert "| overall | 5/10 (0%–50%) | 0/10 (0%–50%) | 0/10 (0%–30%) |" in out
    assert "| agent: categorize |" in out and "| attack: ssrf_steer |" in out
    assert "asked for the planted URL in 5/10" in out and "limits stated here" in out


def test_update_readme_injection_replaces_only_its_block(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(f"a\n{t.INJ_START}\nSTALE\n{t.INJ_END}\nb\n", encoding="utf-8")
    t.update_readme_injection(_inj_doc("injection_eval_baseline", 5), _inj_doc("injection_eval_after", 0), readme)
    text = readme.read_text(encoding="utf-8")
    assert text.startswith("a\n") and text.endswith("b\n") and "STALE" not in text and "| overall |" in text
    with pytest.raises(ValueError):
        t.update_readme_injection(_inj_doc("x", 1), _inj_doc("y", 0), _no_markers(tmp_path))


def _no_markers(tmp_path):
    p = tmp_path / "plain.md"
    p.write_text("none", encoding="utf-8")
    return p


def _generic_doc():
    m = lambda v: {"value": v, "n": 40, "ci_low": 0.1, "ci_high": 0.9}
    return {"name": "judge_calibration", "git_sha": "abc1234", "created_at": "2026-10-06T00:00:00", "cost_usd": 0.5,
            "notes": "a note", "metrics": {"agreement": m(0.45), "cohens_kappa": m(0.04)}}


def test_render_generic_formats_rates_and_kappa():
    out = t.render_generic(_generic_doc())
    assert "| agreement | 45% (10%–90%) | 40 |" in out
    assert "| cohens kappa | 0.04 (0.10 to 0.90) | 40 |" in out
    assert "evals/results/judge_calibration.json" in out and "> a note" in out


def test_update_readme_generic_replaces_only_its_block(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("a\n<!-- judge-calibration:start -->\nOLD\n<!-- judge-calibration:end -->\nb\n", encoding="utf-8")
    t.update_readme_generic("judge-calibration", _generic_doc(), readme)
    text = readme.read_text(encoding="utf-8")
    assert "OLD" not in text and text.startswith("a\n") and text.endswith("b\n")
    with pytest.raises(ValueError):
        t.update_readme_generic("usage-drift-sim", _generic_doc(), readme)


def _has_block(path, name):
    text = path.read_text(encoding="utf-8")
    return f"<!-- {name}:start -->" in text and f"<!-- {name}:end -->" in text


def test_checked_in_docs_have_every_generated_block():
    for name, _, _ in t.GENERIC_BLOCKS:
        assert _has_block(t.DOCS / "monitoring.md", name), name
    for name in ("review-eval", "injection-eval"):
        assert _has_block(t.DOCS / "evaluation.md", name), name



def test_render_generic_can_omit_notes():
    assert "a note" not in t.render_generic(_generic_doc(), notes=False)
