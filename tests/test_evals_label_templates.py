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
