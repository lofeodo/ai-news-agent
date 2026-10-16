"""RSS adapter and the per-topic source dispatcher. No network."""
from datetime import datetime, timedelta, timezone

import pytest

import agent1b_fetch_news as a1b
import config
import rss_source
import topics

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def _rfc822(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>{l}</link><description>{d}</description>"
        + (f"<pubDate>{_rfc822(p)}</pubDate>" if p else "") + "</item>"
        for t, l, d, p in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'.encode()


ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>
<entry><title>Atom story</title><link href="https://example.com/a"/>
<updated>2026-10-17T10:00:00Z</updated>
<summary>Short teaser.</summary>
<content type="html">&lt;p&gt;Full &lt;b&gt;body&lt;/b&gt; text here.&lt;/p&gt;</content></entry></feed>"""


def test_rss_items_become_articles_with_the_shared_shape():
    raw = _rss([("A &amp; B", "https://e.com/1", "<p>Hello <i>world</i></p>", NOW - timedelta(hours=2))])
    [a] = rss_source.parse_feed(raw, {"name": "f", "url": "u"}, NOW - timedelta(hours=168))
    assert a == {"source": "rss", "title": "A & B", "description": "Hello world", "url": "https://e.com/1",
                 "language": "en", "hn_score": None, "_lang_excerpt": ""}


def test_old_items_are_dropped_and_newest_come_first():
    raw = _rss([("old", "https://e.com/old", "", NOW - timedelta(days=30)),
                ("mid", "https://e.com/mid", "", NOW - timedelta(days=2)),
                ("new", "https://e.com/new", "", NOW - timedelta(hours=1))])
    titles = [a["title"] for a in rss_source.parse_feed(raw, {"name": "f", "url": "u"}, NOW - timedelta(hours=168))]
    assert titles == ["new", "mid"]


def test_max_items_limits_a_feed():
    raw = _rss([(f"t{i}", f"https://e.com/{i}", "", NOW - timedelta(hours=i + 1)) for i in range(10)])
    out = rss_source.parse_feed(raw, {"name": "f", "url": "u", "max_items": 3}, NOW - timedelta(hours=168))
    assert [a["title"] for a in out] == ["t0", "t1", "t2"]


def test_undated_items_are_kept():
    raw = _rss([("nodate", "https://e.com/n", "", None)])
    assert len(rss_source.parse_feed(raw, {"name": "f", "url": "u"}, NOW - timedelta(hours=168))) == 1


def test_items_without_link_or_title_are_skipped():
    raw = _rss([("", "https://e.com/1", "", NOW), ("no link", "", "", NOW)])
    assert rss_source.parse_feed(raw, {"name": "f", "url": "u"}, NOW - timedelta(hours=168)) == []


def test_atom_content_feeds_the_excerpt():
    [a] = rss_source.parse_feed(ATOM, {"name": "f", "url": "u"}, NOW - timedelta(hours=168))
    assert a["url"] == "https://example.com/a" and a["description"] == "Short teaser."
    assert a["_lang_excerpt"] == "Full body text here."


def test_garbage_is_an_error_for_that_feed_only():
    with pytest.raises(ValueError):
        rss_source.parse_feed(b"<html><body>not a feed", {"name": "f", "url": "u"}, NOW)


def test_a_dead_feed_is_skipped_not_fatal(capsys):
    good = _rss([("ok", "https://e.com/ok", "", NOW - timedelta(hours=1))])

    def get(url):
        if "dead" in url:
            raise ConnectionError("boom")
        if "junk" in url:
            return b"not xml at all"
        return good

    feeds = [{"name": "dead", "url": "https://dead/x"}, {"name": "junk", "url": "https://junk/x"},
             {"name": "good", "url": "https://good/x"}]
    out = rss_source.fetch_rss_articles(feeds, 168, get=get, now=NOW)
    assert [a["title"] for a in out] == ["ok"]
    log = capsys.readouterr().out
    assert "dead" in log and "junk" in log


# --- dispatcher -----------------------------------------------------------------------------------

def _topic(sources):
    return topics.parse_topic({
        "id": "demo", "name": "Demo", "tagline": "t", "audience": "a", "accent_color": "#112233",
        "languages": ["en"], "variants": ["0_0"], "spotlight": "none",
        "categories": [{"name": "One", "definition": "d"}],
        "sources": sources, "prompts": topics.get_topic("ai").prompts,
    })


@pytest.fixture
def calls(monkeypatch):
    log = []
    monkeypatch.setattr(a1b, "fetch_hn_articles", lambda *a, **k: log.append(("hn", a, k)) or [{"url": "hn"}])
    monkeypatch.setattr(a1b, "fetch_newsapi_articles", lambda *a, **k: log.append(("newsapi", a, k)) or [{"url": "na"}])
    monkeypatch.setattr(a1b, "fetch_rss_articles", lambda feeds, hours: log.append(("rss", feeds, hours)) or [{"url": "rss"}])
    return log


def test_ai_sources_are_called_exactly_as_before(calls):
    out = a1b.fetch_topic_articles(topics.get_topic("ai"))
    assert calls == [("hn", (), {}), ("newsapi", (), {})]
    assert [a["url"] for a in out] == ["hn", "na"]


def test_other_topic_passes_its_own_queries_and_feeds(calls):
    feeds = [{"name": "f", "url": "https://e.com/f.xml"}]
    t = _topic([{"type": "hn", "queries": ["3d printer"], "min_points": 5},
                {"type": "newsapi", "queries": ["q1", "q2"]}, {"type": "rss", "feeds": feeds}])
    out = a1b.fetch_topic_articles(t)
    assert calls == [("hn", (["3d printer"], 5), {}), ("newsapi", (["q1", "q2"],), {}),
                     ("rss", feeds, config.LOOKBACK_HOURS)]
    assert [a["url"] for a in out] == ["hn", "na", "rss"]


def test_hn_without_queries_is_the_plain_weekly_top(calls):
    a1b.fetch_topic_articles(_topic([{"type": "hn"}]))
    assert calls == [("hn", (), {})]


# --- Hacker News with queries -----------------------------------------------------------------------

def test_hn_queries_search_each_term_with_a_points_floor(monkeypatch):
    seen = []

    def fake_search(params):
        seen.append(params)
        return [{"url": f"https://e.com/{params['query']}", "title": "t", "hn_score": 9, "source": "hackernews"}]

    monkeypatch.setattr(a1b, "_hn_search", fake_search)
    out = a1b.fetch_hn_articles(["a", "b"], min_points=10)
    assert [p["query"] for p in seen] == ["a", "b"]
    assert all(p["numericFilters"].endswith(",points>=10") for p in seen)
    assert len(out) == 2


def test_hn_query_failure_skips_that_query(monkeypatch):
    def fake_search(params):
        if params["query"] == "bad":
            raise RuntimeError("x")
        return [{"url": "u"}]

    monkeypatch.setattr(a1b, "_hn_search", fake_search)
    assert a1b.fetch_hn_articles(["bad", "good"]) == [{"url": "u"}]


def test_hn_without_queries_sends_the_original_request(monkeypatch):
    seen = []
    monkeypatch.setattr(a1b, "_hn_search", lambda params: seen.append(params) or [])
    a1b.fetch_hn_articles()
    [p] = seen
    assert "query" not in p and p["tags"] == "story" and p["hitsPerPage"] == a1b.HN_MAX_HITS
    assert p["numericFilters"].startswith("created_at_i>") and "points" not in p["numericFilters"]


def test_newsapi_default_queries(monkeypatch):
    monkeypatch.setenv("NEWS_API_KEY", "k")
    seen = []
    monkeypatch.setattr(a1b, "fetch_newsapi_query", lambda q, t, k: seen.append(q) or [])
    a1b.fetch_newsapi_articles()
    assert seen == config.NEWSAPI_QUERIES
    seen.clear()
    a1b.fetch_newsapi_articles(["only"])
    assert seen == ["only"]
