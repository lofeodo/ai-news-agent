from evals import freeze_baseline as fb


def test_infer_language_french_and_english():
    fr = ("Le gouvernement du Québec a annoncé que les entreprises de la province pourront utiliser "
          "des outils d'intelligence artificielle dans le cadre d'un nouveau programme pour les "
          "petites et moyennes entreprises qui sont en train de se transformer").split()
    assert fb.infer_language(" ".join(fr)) == "fr"
    en = " ".join(["The company announced a new model that can reason about images and code "
                   "while running on cheaper hardware than the previous generation did"] * 2)
    assert fb.infer_language(en) == "en"


def test_infer_language_short_text_defaults_to_english():
    assert fb.infer_language("le la les des") == "en"


def test_pick_sample_keeps_all_shipped_and_is_seeded():
    shipped = ["s1", "s2"]
    other = [f"o{i}" for i in range(20)]
    a = fb.pick_sample(shipped, other, 5, seed=1)
    b = fb.pick_sample(shipped, list(reversed(other)), 5, seed=1)
    assert a == b                      # input order does not matter
    assert a[:2] == shipped and len(a) == 7 and len(set(a)) == 7
    assert fb.pick_sample(shipped, other, 5, seed=2) != a


def test_pick_sample_smaller_pool_than_requested():
    assert fb.pick_sample(["s"], ["o1", "o2"], 10) == ["s", "o1", "o2"]


def test_spearman_identical_and_reversed():
    assert fb.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert fb.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0


def test_spearman_undefined_cases():
    assert fb.spearman([1, 1, 1], [1, 2, 3]) is None   # constant
    assert fb.spearman([1, 2], [1, 2]) is None         # too few
    assert fb.spearman([1, 2, 3], [1, 2]) is None      # length mismatch


def test_top_k_overlap():
    ref = {"a": 9, "b": 8, "c": 7, "d": 1}
    assert fb.top_k_overlap(ref, {"a": 5, "b": 4, "c": 1, "d": 0}, 2) == 1.0
    assert fb.top_k_overlap(ref, {"a": 5, "b": 1, "c": 1, "d": 9}, 2) == 0.5
    assert fb.top_k_overlap(ref, {"a": 1, "b": 1, "c": 5, "d": 9}, 2) == 0.0
    assert fb.top_k_overlap(ref, ref, 10) is None      # fewer ids than k


def test_summarize_outputs():
    items = [
        {"summary": "one two three", "used_fallback": False, "input_tokens": 10, "output_tokens": 3},
        {"summary": None, "used_fallback": True, "input_tokens": 5, "output_tokens": 0},
        {"summary": "four five", "used_fallback": True, "input_tokens": 7, "output_tokens": 2},
    ]
    agg = fb.summarize_outputs(items)
    assert agg["n"] == 3 and agg["n_summarized"] == 2 and agg["n_fallback"] == 2
    assert agg["mean_words"] == 2.5
    assert agg["input_tokens"] == 22 and agg["output_tokens"] == 5


def test_build_articles_fallback_feeds_description_not_text():
    inputs = {
        "shipped": {"u1": {"title": "T1", "language": "fr", "description": "d1", "category": "C", "hn_score": 7,
                           "summary": "prod"}},
        "categories": {"u2": "Cat2"},
        "sources": {"u1": {"text": "full text one", "title": "T1", "used_fallback": False},
                    "u2": {"text": "short snippet only", "title": "T2", "used_fallback": True}},
    }
    rows = fb.build_articles(inputs, ["u1", "u2"])
    r1, r2 = rows
    assert r1["text"] == "full text one" and r1["article"]["language"] == "fr" and r1["shipped"]
    assert r1["production_summary"] == "prod" and not r1["language_inferred"]
    assert r2["text"] is None and r2["article"]["description"] == "short snippet only"
    assert r2["article"]["category"] == "Cat2" and r2["language_inferred"] and not r2["shipped"]
