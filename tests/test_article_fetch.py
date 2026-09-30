import article_fetch as af


class FakeArticle:
    text = ""
    raises = False

    def __init__(self, url, request_timeout=None):
        self.config = type("C", (), {})()

    def download(self):
        if FakeArticle.raises:
            raise RuntimeError("boom")

    def parse(self):
        pass


def _patch(monkeypatch, text="", raises=False):
    FakeArticle.text, FakeArticle.raises = text, raises
    monkeypatch.setattr(af, "Article", FakeArticle)


def test_invalid_url():
    assert af.fetch_article_text_result("ftp://x") == (None, "invalid_url")
    assert af.fetch_article_text("") is None


def test_success_truncates_to_word_limit(monkeypatch):
    _patch(monkeypatch, text="word " * (af.ARTICLE_WORD_LIMIT + 50))
    text, reason = af.fetch_article_text_result("https://example.com/a")
    assert reason is None and len(text.split()) == af.ARTICLE_WORD_LIMIT


def test_reasons(monkeypatch):
    _patch(monkeypatch, text="")
    assert af.fetch_article_text_result("https://e.com")[1] == "empty"
    _patch(monkeypatch, text="short text")
    assert af.fetch_article_text_result("https://e.com")[1] == "too_short"
    _patch(monkeypatch, raises=True)
    assert af.fetch_article_text_result("https://e.com") == (None, "fetch_error")
    assert af.fetch_article_text("https://e.com") is None


def test_agent2b_uses_shared_fetcher():
    import agent2b_summarize_news as a2b
    assert a2b.fetch_article_text is af.fetch_article_text
