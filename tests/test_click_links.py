"""Link map and URL normalisation for click counting. Pure, no network."""
import pytest

import click_links as cl


@pytest.mark.parametrize("raw,expected", [
    ("https://Example.com/a?x=1#top", "https://example.com/a?x=1"),
    ("  http://EXAMPLE.com/Path  ", "http://example.com/Path"),
    ("https://example.com/a?x=1&amp;y=2", "https://example.com/a?x=1&y=2"),
    ("https://example.com", "https://example.com"),
])
def test_normalize(raw, expected):
    assert cl.normalize_url(raw) == expected


@pytest.mark.parametrize("bad", [None, "", "   ", "javascript:alert(1)", "ftp://example.com/x", "//example.com/x",
                                 "mailto:a@b.c", "data:text/html,hi", 5, "https://"])
def test_non_http_urls_are_rejected(bad):
    assert cl.normalize_url(bad) is None and cl.url_key(bad) is None


def test_key_is_stable_and_ignores_cosmetic_differences():
    assert cl.url_key("https://Example.com/a#x") == cl.url_key("https://example.com/a")
    assert cl.url_key("https://example.com/a") != cl.url_key("https://example.com/b")
    assert len(cl.url_key("https://example.com/a")) == 12


DOC = {
    "news_summaries": {
        "Industry & Business": [{"url": "https://news.example/one", "title": "One", "summary": "s"},
                                {"url": "https://news.example/two?a=1&amp;b=2", "title": "Two"}],
        "Policy, Law & Regulation": [{"url": "https://news.example/one", "title": "One again"},
                                     {"url": "javascript:x", "title": "bad"}, {"title": "no url"}],
    },
    "paper_summaries": [{"id": "http://arxiv.org/abs/1", "title": "A Paper", "pdf_url": "https://arxiv.org/pdf/1"},
                        {"title": "no pdf"}],
}


def test_build_link_map_covers_news_and_papers_only_what_shipped():
    m = cl.build_link_map(DOC)
    assert {e["category"] for e in m.values()} == {"Industry & Business", cl.PAPERS_CATEGORY}
    assert len(m) == 3
    assert m[cl.url_key("https://arxiv.org/pdf/1")] == {"url": "https://arxiv.org/pdf/1", "title": "A Paper", "category": "Papers"}


def test_duplicate_url_keeps_first_category():
    m = cl.build_link_map(DOC)
    assert m[cl.url_key("https://news.example/one")]["category"] == "Industry & Business"
    assert m[cl.url_key("https://news.example/one")]["title"] == "One"


def test_empty_or_missing_sections():
    assert cl.build_link_map({}) == {}
    assert cl.build_link_map({"news_summaries": None, "paper_summaries": None}) == {}
    assert cl.build_link_map({"news_summaries": {"A": None}}) == {}


def test_lookup_matches_either_escaping_and_ignores_unshipped_links():
    m = cl.build_link_map(DOC)
    key, entry = cl.lookup(m, "https://news.example/two?a=1&b=2")
    assert entry["title"] == "Two" and key == cl.url_key("https://news.example/two?a=1&amp;b=2")
    assert cl.lookup(m, "https://example.com/unsubscribe?token=abc") is None
    assert cl.lookup(m, "https://lofeodo.com/preferences") is None
    assert cl.lookup(m, None) is None
    assert cl.lookup({}, "https://news.example/one") is None


def test_titles_are_truncated():
    m = cl.build_link_map({"news_summaries": {"A": [{"url": "https://e.com/x", "title": "t" * 500}]}})
    assert len(next(iter(m.values()))["title"]) == 200
