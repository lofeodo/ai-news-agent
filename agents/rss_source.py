# agents/rss_source.py
#
# RSS / Atom feeds as a news source for agent1b. Returns the same article dicts as the Hacker News and
# NewsAPI fetchers. A feed that fails is logged and skipped: one dead feed must never fail the run.

import calendar
import re
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from bs4 import BeautifulSoup

DEFAULT_MAX_ITEMS = 50
FEED_TIMEOUT = 15
DESCRIPTION_CHARS = 500
EXCERPT_CHARS = 400
USER_AGENT = "Mozilla/5.0 (compatible; NewsletterBot/1.0; +https://newsletter.lofeodo.com)"


# BoardGameGeek blog posts carry their own image markup in the feed text, e.g. "[imageid=9200434 medium rep]".
_BBCODE_IMAGE = re.compile(r"\[imageid=\d+[^\]]*\]")


def _plain_text(html: str | None) -> str:
    """Strip tags and collapse whitespace."""
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(" ")
    text = _BBCODE_IMAGE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    return None


def _entry_body(entry) -> str:
    content = entry.get("content")
    if content and isinstance(content, list) and content[0].get("value"):
        return content[0]["value"]
    return entry.get("summary") or ""


def parse_feed(raw_bytes: bytes, feed: dict, cutoff: datetime) -> list:
    """Articles from one feed's raw XML, newest first, at most feed['max_items'], none older than `cutoff`."""
    parsed = feedparser.parse(raw_bytes)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"not a readable feed ({type(parsed.bozo_exception).__name__})")

    entries = []
    for entry in parsed.entries:
        when = _published(entry)
        if when is not None and when < cutoff:
            continue
        entries.append((when, entry))
    # Newest first; an undated entry sorts as if it were brand new (it is in the feed, so it is recent).
    entries.sort(key=lambda pair: pair[0] or datetime.max.replace(tzinfo=timezone.utc), reverse=True)

    articles = []
    for _when, entry in entries[: feed.get("max_items") or DEFAULT_MAX_ITEMS]:
        url   = (entry.get("link") or "").strip()
        title = _plain_text(entry.get("title"))
        if not url or not title:
            continue
        body = _plain_text(_entry_body(entry))
        summary = _plain_text(entry.get("summary"))
        articles.append({
            "source":      "rss",
            "title":       title,
            "description": (summary or body)[:DESCRIPTION_CHARS],
            "url":         url,
            "language":    "en",
            "hn_score":    None,
            # Same role as NewsAPI's: extra evidence for language_filter(), which strips it afterwards.
            # (left empty when the body is just the summary again, so the text isn't shown twice)
            "_lang_excerpt": body[:EXCERPT_CHARS] if body != summary else "",
        })
    return articles


def fetch_rss_articles(feeds: list, lookback_hours: int, get=None, now: datetime | None = None) -> list:
    """Fetch every feed in `feeds` ([{name, url, max_items?}]); returns the merged article list.

    `get(url)` returns the feed's raw bytes (default: requests, which honours HTTPS_PROXY / HTTP_PROXY).
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)

    def _default_get(url: str) -> bytes:
        response = requests.get(url, timeout=FEED_TIMEOUT, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        return response.content

    get = get or _default_get
    print(f"Fetching RSS feeds ({len(feeds)} feeds)...")
    all_articles = []
    for feed in feeds:
        try:
            articles = parse_feed(get(feed["url"]), feed, cutoff)
            print(f"  {feed['name']}: {len(articles)} articles")
            all_articles.extend(articles)
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            print(f"  [rss error] {feed['name']}: {type(e).__name__}{f' (HTTP {status})' if status else ''}: {str(e)[:100]}")
    print(f"  Got {len(all_articles)} RSS articles (before dedup)")
    return all_articles
