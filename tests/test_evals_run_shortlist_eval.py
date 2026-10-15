from evals import run_shortlist_eval as r


def test_retained_counts_pick_level_overlap():
    base = {"A|all": ["u1", "u2", "u3"], "B|all": ["u4"]}
    other = {"A|all": ["u2", "u3", "x"], "B|all": []}
    assert r.retained(base, other) == (2, 4)


def test_retained_missing_key_counts_as_none_kept():
    assert r.retained({"A|all": ["u1"]}, {}) == (0, 1)


def test_picks_by_key():
    sel = {("A", "all"): [{"url": "u1"}, {"url": "u2"}], ("A", "en"): [{"url": "u1"}]}
    assert r.picks_by_key(sel) == {"A|all": ["u1", "u2"], "A|en": ["u1"]}


def test_stage1_survivors():
    base = {"A|all": ["u1", "u2"], "A|en": ["u2", "u3"]}
    assert r.stage1_survivors(base, {"u1", "u2"}) == (3, 4)


def test_load_pool_groups_by_final_category(tmp_path):
    import json
    p = tmp_path / "f.json"
    p.write_text(json.dumps({"articles": [
        {"title": "t1", "url": "u1", "snippet": "s", "language": "fr", "hn_score": 5, "final_category": "A"},
        {"title": "t2", "url": "u2", "final_category": "B"},
        {"title": "t3", "url": "u3", "final_category": None},
    ]}), encoding="utf-8")
    pool = r.load_pool(p)
    assert set(pool) == {"A", "B"} and pool["A"][0]["description"] == "s" and pool["A"][0]["language"] == "fr"
    assert pool["B"][0]["language"] == "en" and pool["B"][0]["hn_score"] is None
