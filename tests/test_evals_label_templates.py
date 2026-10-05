import csv

from evals.make_label_templates import ARTICLE_COLUMNS, build_article_template, make_article_template


def _articles(n=300):
    return [{"id": f"id{i:03d}", "url": f"https://x.test/{i}", "title": f"T{i}", "snippet": "s",
             "first_pass_category": "Policy", "confidence": 2 + (i % 4)} for i in range(n)]


def test_label_column_is_always_blank_and_deterministic():
    a = build_article_template(_articles(), n=100, n_low=40, seed=7)
    b = build_article_template(_articles(), n=100, n_low=40, seed=7)
    assert a == b
    assert len(a) == 100
    assert all(r["gold_category"] == "" for r in a)
    assert a != build_article_template(_articles(), n=100, n_low=40, seed=8)


def test_low_confidence_is_over_represented():
    rows = build_article_template(_articles(), n=100, n_low=40, seed=0)
    low = [r for r in rows if r["sample_stratum"] == "low_confidence"]
    assert len(low) == 40 and all(r["confidence"] < 4 for r in low)
    assert all(r["confidence"] >= 4 for r in rows if r["sample_stratum"] == "high_confidence")


def test_skips_articles_without_audit_and_handles_small_pools():
    arts = _articles(10) + [{"id": "noaudit", "url": "u", "title": "t", "snippet": "", "first_pass_category": None, "confidence": None}]
    rows = build_article_template(arts, n=100, n_low=40)
    assert len(rows) == 10 and all(r["id"] != "noaudit" for r in rows)


def test_csv_written_with_blank_label_column(tmp_path):
    import json
    frozen = tmp_path / "frozen.json"
    frozen.write_text(json.dumps({"articles": _articles(50)}), encoding="utf-8")
    out = make_article_template(frozen, tmp_path / "t.csv", n=20, n_low=8)
    with out.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert reader.fieldnames == ARTICLE_COLUMNS
    assert len(rows) == 20 and all(r["gold_category"] == "" for r in rows)


def _news(n=40):
    items = []
    for i in range(n):
        items.append({"url": f"https://n.test/{i}", "title": f"N{i}", "description": f"desc {i}",
                      "summary": f"sum {i}", "used_fallback": i % 5 == 0})
    items.append({"url": "https://twitter.com/x/1", "title": "tw", "description": "d", "summary": "s", "used_fallback": False})
    items.append({"url": "https://n.test/nosummary", "title": "ns", "description": "d", "summary": None, "used_fallback": False})
    return items


def _papers(n=15):
    return [{"id": f"p{i}", "title": f"P{i}", "pdf_url": f"https://p.test/{i}", "summary": f"ps {i}", "used_fallback": False}
            for i in range(n)]


def test_summary_template_sources_blank_label_and_private_text(tmp_path):
    from evals.make_label_templates import build_summary_template
    rows, skipped = build_summary_template(
        _news(), _papers(), lambda url: f"article text of {url}", lambda url, pid: f"pdf text {pid}",
        n_full=10, n_fallback=3, n_papers=4, seed=1, private_dir=tmp_path)
    assert len(rows) == 17 and skipped == 0
    assert all(r["supported"] == "" for r in rows)
    assert sum(r["kind"] == "paper" for r in rows) == 4
    assert sum(r["used_fallback"] for r in rows) == 3
    assert all(not r["url"].startswith("https://twitter.com") for r in rows)
    for r in rows:
        assert (tmp_path / "summary_sources" / f"{r['id']}.txt").exists()
    assert "article text" not in "".join(r["generated_summary"] + r["title"] for r in rows)


def test_summary_template_skips_failed_fetches_and_is_deterministic(tmp_path):
    from evals.make_label_templates import build_summary_template
    flaky = lambda url: None if url.endswith(("1", "2", "3")) else "text"
    args = (_news(), _papers(), flaky, lambda url, pid: None)
    a, skipped = build_summary_template(*args, n_full=10, n_fallback=2, n_papers=3, seed=3, private_dir=tmp_path)
    b, _ = build_summary_template(*args, n_full=10, n_fallback=2, n_papers=3, seed=3, private_dir=tmp_path)
    assert a == b and skipped > 0
    assert sum(r["kind"] == "news" and not r["used_fallback"] for r in a) == 10
    assert not any(r["kind"] == "paper" for r in a)
