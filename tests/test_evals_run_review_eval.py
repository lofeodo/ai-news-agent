import pytest

from evals import cost, review_eval, run_review_eval as runner
from agent1b_graph import ReviewConfig
from tests.fakes import CATS, FakeClient, Fetcher, make_articles


def _articles(n):
    arts = make_articles(n)
    for i, a in enumerate(arts):
        a["_id"] = f"id{i}"
    return arts


GOLD = {
    "id0": {"gold": CATS[1], "stratum": "high_confidence"},
    "id1": {"gold": CATS[5], "stratum": "low_confidence"},   # review fixes this one
    "id2": {"gold": CATS[1], "stratum": "low_confidence"},   # review breaks this one
    "id3": {"gold": CATS[5], "stratum": "high_confidence"},
}
CONFIDENCES = {"T0": 5, "T1": 2, "T2": 2, "T3": 5, "T4": 2, "T5": 5}


def _run(**client_kw):
    client = FakeClient(CONFIDENCES, **client_kw)
    fetcher = Fetcher()
    rows, usage = runner.run_eval(_articles(6), GOLD, client, fetcher, ReviewConfig())
    return client, fetcher, rows, usage


def test_run_eval_scores_only_labeled_articles_and_reviews_only_low_confidence_ones():
    client, _, rows, usage = _run()
    assert sorted(r["id"] for r in rows) == ["id0", "id1", "id2", "id3"]
    assert {r["id"] for r in rows if r["routed"]} == {"id1", "id2"}   # id4 is low-confidence but unlabeled
    assert client.n("review") == 2
    assert usage["gold_total"] == 4 and usage["gold_in_frozen"] == 4


def test_run_eval_final_category_comes_from_review():
    _, _, rows, _ = _run()
    by_id = {r["id"]: r for r in rows}
    assert by_id["id1"]["first_pass"] == CATS[1] and by_id["id1"]["final"] == CATS[5]
    assert by_id["id0"]["final"] == by_id["id0"]["first_pass"] == CATS[1]
    assert by_id["id0"]["single_pass"] == CATS[1] and by_id["id0"]["confidence"] == 5


def test_run_eval_feeds_paired_scoring():
    _, _, rows, _ = _run()
    paired = review_eval.paired_metrics(rows)["paired_review_effect"]
    assert (paired["wins"], paired["losses"]) == (1, 1)


def test_run_eval_degrades_to_first_pass_when_review_fails():
    _, _, rows, _ = _run(review="raise")
    routed = [r for r in rows if r["routed"]]
    assert routed and all(r["final"] == r["first_pass"] and r["review_status"] == "review_failed" for r in routed)


def test_run_eval_meters_tokens_and_cost():
    _, _, rows, usage = _run()
    assert usage["single_pass"]["input"] > 0 and usage["graph_categorize"]["output"] > 0
    assert usage["review"]["articles"] == 2 and usage["review"]["input"] == sum(r["input_tokens"] for r in rows)
    assert runner.total_cost(usage) > 0
    metrics = runner.usage_metrics(rows, usage)
    assert metrics["coverage_scored_by_both_arms"]["n"] == 4 and metrics["coverage_scored_by_both_arms"]["value"] == 1.0
    assert metrics["tokens_input__review"]["n"] == 2


def test_reconcile_gold_repairs_mangled_ids_by_url():
    arts = _articles(2)
    gold = {"id0": {"gold": "A", "stratum": "s", "url": arts[0]["url"]},
            "5.19E+11": {"gold": "B", "stratum": "s", "url": arts[1]["url"]},
            "ghost": {"gold": "C", "stratum": "s", "url": "https://nowhere"}}
    assert set(runner.reconcile_gold(gold, arts)) == {"id0", "id1", "ghost"}


def test_cached_fetcher_hits_network_once_and_persists(tmp_path):
    inner = Fetcher()
    path = tmp_path / "cache.json"
    f = runner.CachedFetcher(inner, path)
    assert f("https://x/1", 5) == f("https://x/1", 5)
    assert inner.urls == ["https://x/1"]
    f.save()
    assert runner.CachedFetcher(Fetcher(raises=RuntimeError("no network")), path)("https://x/1", 5)[0]


def test_estimate_grows_with_review_articles_and_guard_blocks_big_runs():
    arts = _articles(10)
    small = runner.estimate_tokens(arts, 0)
    assert runner.estimate_tokens(arts, 40)[0] > small[0]
    with pytest.raises(cost.CostLimitExceeded):
        cost.CostGuard().check(5.0)


def test_main_dry_run_calls_no_api(capsys, monkeypatch):
    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: pytest.fail("dry run must not create a client"))
    assert runner.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "estimated worst-case cost" in out and "gold labels: 100" in out
