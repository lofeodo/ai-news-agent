from types import SimpleNamespace

import pytest

from evals import dedup_eval as de


def run(groups=(), removed=(), failed=False, tin=100, tout=10, secs=1.0):
    return {"groups": [list(g) for g in groups], "removed": list(removed), "failed": failed,
            "input_tokens": tin, "output_tokens": tout, "seconds": secs}


def row(case_id="c1", kind="real", ids=("a", "b", "c", "d"), gold=(("a", "b"),), border=(), **runs):
    return {"case_id": case_id, "kind": kind, "category": "X", "article_ids": list(ids),
            "hn": {i: None for i in ids}, "gold_groups": [sorted(g) for g in gold],
            "borderline": list(border), "runs": {k: list(v) for k, v in runs.items()}}


def test_prepare_cases_drops_excluded_and_attaches_gold():
    cases = [{"case_id": "c1", "kind": "real", "category": "X",
              "articles": [{"id": "a", "hn_score": 5}, {"id": "b"}, {"id": "c"}]}]
    gold = SimpleNamespace(groups={"c1": [frozenset({"a", "b"})]}, excluded={"c1": {"c"}},
                           borderline={"c1": {"b", "zzz"}})
    rows, arts = de.prepare_cases(cases, gold)
    assert rows[0]["article_ids"] == ["a", "b"]
    assert [a["id"] for a in arts["c1"]] == ["a", "b"]
    assert rows[0]["gold_groups"] == [["a", "b"]]
    assert rows[0]["borderline"] == ["b"]
    assert rows[0]["hn"] == {"a": 5, "b": None}


def test_recall_counts_gold_pairs_found():
    rows = [row(haiku=[run([("a", "b")], ["b"])], sonnet=[run([], [])])]
    assert de.recall(rows, "haiku")["value"] == 1.0
    assert de.recall(rows, "sonnet")["value"] == 0.0
    assert de.recall(rows, "sonnet")["n"] == 1


def test_recall_group_of_three_needs_all_pairs():
    r = row(ids=("a", "b", "c", "d"), gold=(("a", "b", "c"),), m=[run([("a", "b")], ["b"])])
    assert de.recall([r], "m")["value"] == pytest.approx(1 / 3)


def test_control_arm_has_full_residual_and_no_false_removal():
    r = row(control=[run()])
    assert de.residual_duplicate_rate([r], "control")["value"] == 1.0
    assert de.false_removal_rate([r], "control")["value"] == 0.0
    assert de.recall([r], "control")["value"] == 0.0


def test_correct_removal_leaves_no_residual():
    r = row(m=[run([("a", "b")], ["a"])])
    assert de.residual_duplicate_rate([r], "m")["value"] == 0.0
    assert de.false_removal_rate([r], "m")["value"] == 0.0


def test_false_removal_of_unique_article():
    # should-be-kept articles: c, d, and one of a/b = 3
    r = row(m=[run([("a", "c")], ["c"])])
    fr = de.false_removal_rate([r], "m")
    assert (fr["value"], fr["n"]) == (pytest.approx(1 / 3), 3)


def test_removing_both_group_members_is_a_false_removal():
    r = row(m=[run([("a", "b")], ["a", "b"])])
    assert de.false_removal_rate([r], "m")["value"] == pytest.approx(2 / 3)


def test_false_group_rate_pair_level():
    r = row(m=[run([("a", "b"), ("c", "d")], ["b", "d"])])
    fg = de.false_group_rate([r], "m")
    assert (fg["value"], fg["n"]) == (0.5, 2)


def test_borderline_strict_vs_broad():
    # c is borderline; the arm groups c with d and removes d: strict mistake, broad ignored
    r = row(border=("c",), m=[run([("a", "b"), ("c", "d")], ["b", "c"])])
    assert de.false_group_rate([r], "m")["value"] == 0.5
    assert de.false_group_rate([r], "m", broad=True)["value"] == 0.0
    strict = de.false_removal_rate([r], "m")
    broad = de.false_removal_rate([r], "m", broad=True)
    assert strict["value"] > 0 and broad["value"] == 0.0


def test_case_exact_match_and_failure_rate():
    rows = [row(m=[run([("a", "b")], ["b"]), run([], [], failed=True)])]
    assert de.case_exact_match(rows, "m")["value"] == 0.5
    assert de.failure_rate(rows, "m")["value"] == 0.5


def test_per_kind_metrics_split():
    rows = [row("c1", "real", m=[run([("a", "b")], ["b"])]), row("c2", "control", gold=(), m=[run()])]
    metrics, _ = de.score(rows, cheap="m", strong="zz")
    assert metrics["duplicate_recall__m__real"]["value"] == 1.0
    assert metrics["residual_duplicate_rate__m__real"]["value"] == 0.0
    assert "duplicate_recall__m__control" in metrics


def test_paired_sonnet_vs_haiku_wins_and_losses():
    rows = [row("c1", haiku=[run()], sonnet=[run([("a", "b")], ["b"])]),
            row("c2", haiku=[run([("a", "b")], ["b"])], sonnet=[run([("a", "b")], ["b"])])]
    metrics, _ = de.score(rows)
    p = metrics["paired_recall_sonnet_vs_haiku"]
    assert (p["wins"], p["losses"], p["ties"], p["items"]) == (1, 0, 1, 2)


def test_paired_false_removal():
    rows = [row(haiku=[run([("a", "c")], ["c"])], sonnet=[run([("a", "b")], ["b"])])]
    p = de.score(rows)[0]["paired_false_removal_sonnet_vs_haiku"]
    assert p["wins"] == 1 and p["losses"] == 0


def test_usage_metrics():
    rows = [row(m=[run(tin=100, tout=10, secs=1.0), run(tin=300, tout=30, secs=3.0)])]
    u = de.usage_metrics(rows, "m")
    assert u["tokens_input__m"]["value"] == 400
    assert u["seconds_mean__m"]["value"] == 2.0
    assert u["seconds_p95__m"]["value"] == 3.0


def test_verdict_prefers_cheap_when_intervals_overlap():
    rows = [row(haiku=[run([("a", "b")], ["b"])], sonnet=[run([("a", "b")], ["b"])])]
    assert "No clear winner" in de.score(rows)[1]


def test_verdict_picks_strong_when_clearly_better():
    rows = [row(f"c{i}", haiku=[run()], sonnet=[run([("a", "b")], ["b"])]) for i in range(40)]
    assert de.score(rows)[1].startswith("Use sonnet")


def test_single_model_verdict_and_control_first_order():
    rows = [row(haiku=[run()], control=[run()])]
    assert de.arms_present(rows) == ["control", "haiku"]
    assert "Only one model" in de.score(rows)[1]
