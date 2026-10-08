from evals.make_dedup_cases import (COLUMNS, build_dedup_cases, build_synthetic_cases, is_repost, overlap,
                                    template_rows, tokens, write_outputs)


def art(i, title, cat="Industry & Business", desc=""):
    return {"url": f"https://x.test/{i}", "title": title, "description": desc, "summary": desc,
            "category": cat, "language": "en", "source": "newsapi", "hn_score": None}


def pool():
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet",
             "kilo", "lima", "mike", "november", "oscar", "papa", "quebec", "romeo", "sierra", "tango"]
    items = [art(i, f"{w}one {w}two {w}three {w}four") for i, w in enumerate(words)]
    items.append(art(100, "Acme launches Rocket model today", desc="Acme released the Rocket model"))
    items.append(dict(art(101, "Acme Rocket model launched", desc="Acme released the Rocket model today"),
                      url="https://other.test/101"))
    return items


def test_tokens_and_overlap():
    assert "rocket" in tokens(art(1, "The Rocket model"))
    assert overlap({"a"}, set()) == 0.0
    assert overlap({"a", "b"}, {"a", "b", "c"}) == 1.0


def test_real_case_contains_suspected_pair_and_is_sized():
    cases = build_dedup_cases(pool(), n_real=1, n_hard=0, n_control=0, size=8)
    assert len(cases) == 1
    c = cases[0]
    urls = {a["url"] for a in c["articles"]}
    assert {"https://x.test/100", "https://other.test/101"} <= urls
    assert len(c["articles"]) == 8 and c["kind"] == "real"


def test_deterministic_for_seed():
    a = build_dedup_cases(pool(), n_real=1, n_hard=0, n_control=1, seed=3)
    b = build_dedup_cases(pool(), n_real=1, n_hard=0, n_control=1, seed=3)
    assert a == b


def test_control_has_no_suspected_pair_and_no_reuse():
    cases = build_dedup_cases(pool(), n_real=1, n_hard=0, n_control=1)
    ids = [a["id"] for c in cases for a in c["articles"]]
    assert len(ids) == len(set(ids))
    assert any(c["kind"] == "control" for c in cases)


def test_template_labels_always_blank(tmp_path):
    cases = build_dedup_cases(pool(), n_real=1, n_hard=0, n_control=1)
    rows = template_rows(cases)
    assert rows and all(r["duplicate_group"] == "" and r["note"] == "" for r in rows)
    cp, tp = write_outputs(cases, tmp_path / "c.json", tmp_path / "t.csv")
    assert tp.read_text(encoding="utf-8-sig").splitlines()[0] == ",".join(COLUMNS)


def test_synthetic_case_adds_rewrite_next_to_source():
    items = pool()
    syn = [{"source_url": "https://x.test/100", "title": "Rocket from Acme arrives", "summary": "A new model."}]
    cases = build_synthetic_cases(syn, items)
    assert len(cases) == 1 and cases[0]["kind"] == "synthetic"
    urls = [a["url"] for a in cases[0]["articles"]]
    assert "https://x.test/100" in urls and any(u.startswith("synthetic:") for u in urls)
    assert build_synthetic_cases([{"source_url": "nope", "title": "t", "summary": "s"}], items) == []


def test_reposts_are_not_proposed_as_pairs():
    a = art(1, "Anthropic vertical software rollout threatens builders")
    b = art(2, "Anthropic Vertical Software Rollout Threatens Builders")
    c = art(3, "Totally different headline words here")
    c["url"] = "https://x.test/3"
    assert is_repost(a, b)
    d = dict(art(4, "Rollout of vertical software by Anthropic worries builders"), url="https://other.test/4")
    assert not is_repost(a, d)
    cases = build_dedup_cases([a, b] + pool()[:20], n_real=1, n_hard=0, n_control=0)
    assert cases == [] or not {"https://x.test/1", "https://x.test/2"} <= {x["url"] for x in cases[0]["articles"]}


def _sec(cat, titles):
    return {"category": cat, "articles": [art(f"{cat}{i}", t) | {"url": f"https://s{i}.test/{cat}/{i}"}
                                           for i, t in enumerate(titles)]}


def test_shipped_cases_flag_suspect_sections_and_sample_controls():
    from evals.make_dedup_cases import build_shipped_cases
    runs = [{"run_id": "r1", "sections": [
        _sec("A", ["Acme launches Rocket model", "Acme Rocket model released today", "Totally unrelated tulips"]),
        _sec("B", ["alpha beta gamma delta", "epsilon zeta eta theta", "iota kappa lambda mu"]),
        _sec("C", ["only", "two"]),
    ]}]
    cases = build_shipped_cases(runs, n_controls=5)
    kinds = {c["category"]: c["kind"] for c in cases}
    assert kinds == {"A": "shipped", "B": "shipped_control"}      # C has fewer than 3 articles
    assert all(c["run_id"] == "r1" for c in cases)
    # sections that share an article with the already-labeled cases are left out
    skip = build_shipped_cases(runs, exclude_urls={"https://s0.test/A/0"})
    assert [c["category"] for c in skip] == ["B"]


def test_regenerating_never_overwrites_labels(tmp_path):
    import pytest
    from evals.make_dedup_cases import _refuse_if_labeled
    p = tmp_path / "t.csv"
    p.write_text("case_id,article_id,title,snippet,kind,duplicate_group,note\nc,a,t,s,real,g1,\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _refuse_if_labeled(p)
    p.write_text("case_id,article_id,title,snippet,kind,duplicate_group,note\nc,a,t,s,real,,\n", encoding="utf-8")
    _refuse_if_labeled(p)
