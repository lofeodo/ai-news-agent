from evals import run_abstract_first_eval as ev

UP = {"a": 1, "b": 5, "c": 0, "d": 9}


def test_ranking_total_then_upvotes_then_id():
    totals = {"a": 10, "b": 10, "c": 30, "d": 10}
    assert ev.ranking(totals, UP) == ["c", "d", "b", "a"]


def test_best_and_rank_of():
    totals = {"a": 5, "b": 9, "c": 7}
    order = ev.ranking(totals, {})
    assert ev.best(totals, {}) == "b"
    assert ev.rank_of("c", order) == 2 and ev.rank_of("zzz", order) is None
    assert ev.in_top_k("c", order, 2) and not ev.in_top_k("a", order, 2) and not ev.in_top_k("zzz", order, 9)


def test_best_of_nothing_is_none():
    assert ev.best({}, {}) is None


def test_pass_totals_skips_failed_scores():
    items = [{"id": "a", "passes": [{"scores": {"total": 30}}, {"scores": None}]},
             {"id": "b", "passes": [{"scores": {"total": 10}}, {"scores": {"total": 12}}]}]
    assert ev.pass_totals(items, 0) == {"a": 30, "b": 10}
    assert ev.pass_totals(items, 1) == {"b": 12}


def test_recall_table_counts_pairs():
    refs = [{"a": 9, "b": 1, "c": 1}, {"b": 9, "a": 1, "c": 1}]      # best: a, then b
    approx = [ev.ranking({"a": 5, "b": 4, "c": 1}, {}),               # a first
              ev.ranking({"c": 5, "b": 4, "a": 3}, {})]               # a third
    t = ev.recall_table(approx, refs, {}, ks=(1, 2, 3))
    # pairs: (approx0, ref0)->a rank1; (approx0, ref1)->b rank2; (approx1, ref0)->a rank3; (approx1, ref1)->b rank2
    assert t["ranks"] == [1, 2, 3, 2]
    assert t["by_k"] == {1: (1, 4), 2: (3, 4), 3: (4, 4)}
