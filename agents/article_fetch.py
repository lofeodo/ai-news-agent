# agents/article_fetch.py
#
# Article-text fetching shared by agent2b (summaries) and agent1b (review loop).
# Moved out of agent2b_summarize_news.py unchanged in behavior.

import base64
import os
import re
import sys
import threading
from urllib.parse import urlparse

import requests
from newspaper import Article

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ARTICLE_WORD_LIMIT

# newspaper3k's parse() runs lxml/libxml2 (and, with fetch_images on, Pillow)
# C code that isn't safe to run from many threads at once — concurrent parses
# are the prime suspect for the free()/munmap_chunk() SIGABRTs that killed
# agent2b on 2026-09-07 and 2026-09-28. Downloads stay parallel; parsing is
# CPU-bound under the GIL anyway, so serializing it costs little. Module-level
# so every caller in the process shares the one lock.
_parse_lock = threading.Lock()

FETCH_TIMEOUT     = 10
MIN_ARTICLE_WORDS = 100

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_GITHUB_NAME_RE = re.compile(r'^[A-Za-z0-9_.-]+$')


def _parse_github_repo(url: str) -> tuple[str, str] | None:
    """Return (owner, repo) if url points to a GitHub repo, else None."""
    try:
        parsed = urlparse(url)
        if parsed.hostname not in ("github.com", "www.github.com"):
            return None
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            return None
        owner, repo = parts[0], parts[1]
        if not _GITHUB_NAME_RE.match(owner) or not _GITHUB_NAME_RE.match(repo):
            return None
        return owner, repo
    except Exception:
        return None


def _fetch_github_repo_text(url: str) -> str | None:
    """Fetch repo description + README via GitHub API (no auth needed for public repos)."""
    parsed = _parse_github_repo(url)
    if not parsed:
        return None
    owner, repo = parsed
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    try:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers, timeout=10)
        r.raise_for_status()
        meta  = r.json()
        parts = [f"{meta.get('name', repo)}: {meta.get('description', '')}"]
        if meta.get("topics"):
            parts.append("Topics: " + ", ".join(meta["topics"]))

        readme_r = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/readme", headers=headers, timeout=10
        )
        if readme_r.status_code == 200:
            raw = base64.b64decode(readme_r.json().get("content", "")).decode("utf-8", errors="replace")
            raw = re.sub(r"#{1,6}\s+", "", raw)
            words = raw.split()[:ARTICLE_WORD_LIMIT]
            parts.append(" ".join(words))

        return "\n\n".join(p for p in parts if p) or None
    except Exception:
        return None


def fetch_article_text_result(url: str, timeout: int = FETCH_TIMEOUT) -> tuple[str | None, str | None]:
    """Fetch article text. Returns (text, None) on success or (None, reason).

    reason is one of: "invalid_url", "github_unavailable", "fetch_error",
    "empty", "too_short". Callers that only need the text use
    fetch_article_text(); the review loop uses the reason so the model can be
    told *why* there is no text.
    """
    if not url or not url.startswith(("https://", "http://")):
        return None, "invalid_url"
    if _parse_github_repo(url):
        text = _fetch_github_repo_text(url)
        return (text, None) if text else (None, "github_unavailable")
    try:
        article = Article(url, request_timeout=timeout)
        article.config.browser_user_agent = USER_AGENT
        article.config.fetch_images = False  # summaries never use images
        article.download()
        with _parse_lock:
            article.parse()
        text = article.text.strip()
        if not text:
            return None, "empty"
        words = text.split()
        if len(words) < MIN_ARTICLE_WORDS:
            return None, "too_short"
        if len(words) > ARTICLE_WORD_LIMIT:
            words = words[:ARTICLE_WORD_LIMIT]
        return " ".join(words), None
    except Exception:
        return None, "fetch_error"


def fetch_article_text(url: str) -> str | None:
    return fetch_article_text_result(url)[0]
