import json

from evals.snapshot import article_id, freeze_articles, join_articles, save_private_text, snippet


def _news():
    return {"run_at": "2026-09-30", "articles": [
        {"url": "https://a.test/1", "title": "A", "description": "x " * 400, "source": "hackernews",
         "language": "en", "hn_score": 10, "category": "Industry & Business"},
        {"url": "https://a.test/2", "title": "B", "description": "short", "source": "newsapi",
         "language": "fr", "hn_score": None, "category": "Policy"},
    ]}


def _review():
    return {"run_id": "r1", "articles": [
        {"url": "https://a.test/1", "first_pass_category": "Policy", "confidence": 3,
         "review_status": "reviewed", "final_category": "Industry & Business"},
    ]}


def test_article_id_is_stable_and_short():
    assert article_id("https://a.test/1") == article_id("https://a.test/1")
    assert len(article_id("https://a.test/1")) == 12


def test_snippet_truncates_and_collapses_whitespace():
    assert snippet("a   b\n c") == "a b c"
    assert len(snippet("word " * 200)) <= 300
    assert snippet(None) == ""


def test_join_articles_merges_audit_by_url():
    rows = join_articles(_news(), _review())
    assert rows[0]["first_pass_category"] == "Policy" and rows[0]["confidence"] == 3
    assert rows[0]["final_category"] == "Industry & Business"
    assert rows[1]["first_pass_category"] is None and rows[1]["confidence"] is None
    assert len(rows[0]["snippet"]) <= 300


def test_freeze_articles_writes_file(tmp_path):
    (tmp_path / "news_filtered.json").write_text(json.dumps(_news()), encoding="utf-8")
    (tmp_path / "agent1b_review_log.json").write_text(json.dumps(_review()), encoding="utf-8")
    out = freeze_articles(tmp_path, tmp_path / "out.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["review_run_id"] == "r1" and len(data["articles"]) == 2


def test_save_private_text(tmp_path):
    path = save_private_text("articles", "abc", "full text", private_dir=tmp_path)
    assert path.read_text(encoding="utf-8") == "full text"
    assert path.parent.name == "articles"
