# agents/click_links.py
#
# The map from "a URL someone clicked" to "which shipped article, in which category", built from the
# pipeline run doc by agent4 at send time and stored as click_links/{run_id}. SendGrid's click events carry
# the original target URL (and our run_id as a custom arg), so no link in the newsletter is rewritten by us.
# Pure: no Firestore, no network.

import hashlib
import html
from urllib.parse import urlsplit, urlunsplit

PAPERS_CATEGORY = "Papers"


def normalize_url(url) -> str | None:
    """Canonical form used on both sides of the lookup, or None if it is not a usable http(s) URL.

    Lower-cases scheme and host, drops the fragment and surrounding whitespace, and decodes HTML entities
    (the newsletter HTML escapes `&` as `&amp;`, and a tracker may report either form).
    """
    if not isinstance(url, str):
        return None
    parts = urlsplit(html.unescape(url).strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def url_key(url) -> str | None:
    """Short stable id of a URL, safe to use as a Firestore field name."""
    norm = normalize_url(url)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12] if norm else None


def build_link_map(run_doc: dict) -> dict[str, dict]:
    """{url_key: {url, title, category}} for every article and paper that shipped in this run.

    Reads `news_summaries` (agent3 prunes it to what shipped, grouped by category) and `paper_summaries`
    (linked by `hf_url`, else `pdf_url`). Only public titles and URLs are kept. A URL that appears twice keeps its first entry.
    """
    out: dict[str, dict] = {}

    def add(url, title, category):
        key = url_key(url)
        if key and key not in out:
            out[key] = {"url": normalize_url(url), "title": (title or "")[:200], "category": category}

    for category, articles in (run_doc.get("news_summaries") or {}).items():
        for a in articles or []:
            add(a.get("url"), a.get("title"), category)
    for p in run_doc.get("paper_summaries") or []:
        add(p.get("hf_url") or p.get("pdf_url"), p.get("title"), PAPERS_CATEGORY)
    return out


def lookup(link_map: dict, event_url) -> tuple[str, dict] | None:
    """(key, entry) for a clicked URL, or None for anything we did not ship (footer, unsubscribe, preferences...)."""
    key = url_key(event_url)
    entry = link_map.get(key) if key else None
    return (key, entry) if entry else None
