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
    readme.write_text(f"before\n{START}\nold\n{END}\nafter\n", encoding="utf-8")
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


def test_checked_in_readme_has_markers():
    assert START in t.README.read_text(encoding="utf-8") and END in t.README.read_text(encoding="utf-8")
