"""Bake the latest issue's excerpt into the landing page.

The landing hero shows the spotlight paper plus the first story of the first three sections. That
text is copied from the latest release issue (the public ``/issues/latest`` page) and written straight into
``public/newsletter/index.html`` between the ``EXCERPT:START`` / ``EXCERPT:END`` markers, so the
deployed page carries it in its own HTML: no runtime request, nothing that can be slow or cold.

Run it just before a hosting deploy (``cloudbuild-site.yaml`` does both). Any failure (fetch, parse,
validation) exits non-zero *without touching the file*, so the deploy step never runs and the
previous site, with the previous issue's excerpt, stays live.

    python tools/bake_landing_excerpt.py
    python tools/bake_landing_excerpt.py --source saved_issue.html   # offline
"""
import argparse
import html
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

DEFAULT_SOURCE = "https://agent-subscriptions-zozrn33sna-nn.a.run.app/issues/latest"
DEFAULT_INDEX = Path(__file__).resolve().parent.parent / "public" / "newsletter" / "index.html"
MARK_START = "<!-- EXCERPT:START -->"
MARK_END = "<!-- EXCERPT:END -->"
MAX_STORIES = 3
MIN_STORIES = 2

_INVISIBLE = re.compile("[​-‏⁠﻿­͏]")
_SKIP_TAGS = {"style", "script", "title", "head"}
_VOTES = re.compile(r"^▲\s*[\d,]+$")
_DATE = re.compile(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$")


class _Text(HTMLParser):
    """Visible text nodes, in document order, whitespace-collapsed (preheader padding dropped)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip:
            return
        text = re.sub(r"\s+", " ", _INVISIBLE.sub("", data)).strip()
        if text:
            self.nodes.append(text)


def parse_issue(issue_html: str) -> dict:
    """Pull the paper card and first story per section out of a composed issue.

    Reads the sequence of text nodes the email renders (paper label, votes, "upvotes", title,
    authors, summary, "Read on ...", then per section: number, name, title, summary, "HN Score"),
    so it does not depend on the template's tags or inline styles. Raises ValueError if the issue
    does not look as expected.
    """
    p = _Text()
    p.feed(issue_html)
    t = p.nodes

    i_paper = next((i for i, s in enumerate(t) if re.search(r"paper of the week", s, re.I)), -1)
    i_up = next((i for i in range(max(i_paper, 0) + 1, len(t)) if re.fullmatch(r"upvotes?", t[i], re.I)), -1)
    if i_paper < 0 or i_up < 1 or not _VOTES.match(t[i_up - 1]) or i_up + 4 >= len(t):
        raise ValueError("paper card not found")
    paper = {"votes": t[i_up - 1], "title": t[i_up + 1], "by": t[i_up + 2], "summary": t[i_up + 3]}
    if not re.match(r"read on", t[i_up + 4], re.I) or len(paper["title"]) > 220 or len(paper["summary"]) < 40:
        raise ValueError("paper card is malformed")

    stories = []
    k = i_up + 5
    while k < len(t) - 3 and len(stories) < MAX_STORIES:
        body = t[k + 3]
        if re.fullmatch(r"\d{2}", t[k]) and re.search(r"[A-Za-z]", t[k + 1]) and len(body) >= 40:
            has_score = t[k + 4:k + 5] == ["HN Score"] and bool(_VOTES.match((t[k + 5:k + 6] or [""])[0]))
            stories.append({
                "n": t[k],
                "section": re.sub(r"^[^A-Za-z]+", "", t[k + 1]),
                "title": t[k + 2],
                "summary": body,
                "score": t[k + 5] if has_score else "",
            })
            k += 4
        else:
            k += 1
    if len(stories) < MIN_STORIES:
        raise ValueError(f"only {len(stories)} story/stories found")

    date = next((s for s in t if _DATE.match(s)), "")
    if not date:
        raise ValueError("issue date not found")
    return {"date": date, "paper": paper, "stories": stories}


def render_excerpt(data: dict) -> str:
    """The figure + caption markup. Every value is escaped; nothing is passed through as HTML."""
    def e(value: str) -> str:
        return html.escape(value, quote=False)  # text nodes only; no value lands in an attribute

    pp = data["paper"]
    story_html = "\n".join(
        f"""              <article class="story">
                <p class="story__sec"><span><b>{e(s['n'])}</b> {e(s['section'])}</span><span>{e(s['score'])}</span></p>
                <h4>{e(s['title'])}</h4>
                <p>{e(s['summary'])}</p>
              </article>"""
        for s in data["stories"]
    )
    return f"""<figure class="issue" aria-label="Excerpt from the latest issue">
          <div class="issue__bar"><i></i><i></i><i></i><span>Latent SpaceMail · {e(data['date'])}</span></div>
          <div class="issue__body">
            <article class="paper">
              <div class="paper__meta"><span>★ Paper of the week</span><b>{e(pp['votes'])} upvotes</b></div>
              <h3>{e(pp['title'])}</h3>
              <p class="paper__by">{e(pp['by'])}</p>
              <p>{e(pp['summary'])}</p>
              <span class="paper__cta">Read on Hugging Face →</span>
            </article>

            <div class="stories">
{story_html}
            </div>
          </div>
        </figure>
        <p class="issue-caption"><span>From the issue of {e(data['date'])}.</span><a href="/issues.html">Read the latest issue →</a></p>"""


def bake(index_html: str, data: dict) -> str:
    if index_html.count(MARK_START) != 1 or index_html.count(MARK_END) != 1:
        raise ValueError("index.html must contain exactly one EXCERPT:START and EXCERPT:END marker")
    head, rest = index_html.split(MARK_START)
    _, tail = rest.split(MARK_END)
    return f"{head}{MARK_START}\n        {render_excerpt(data)}\n        {MARK_END}{tail}"


def fetch(source: str, attempts: int = 4, timeout: int = 45) -> str:
    """URL or file path. The backend scales to zero, so a cold first request is retried."""
    if not source.startswith(("http://", "https://")):
        return Path(source).read_text(encoding="utf-8")
    last = None
    for n in range(attempts):
        try:
            with urllib.request.urlopen(source, timeout=timeout) as r:
                return r.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - report and retry
            last = exc
            print(f"fetch attempt {n + 1}/{attempts} failed: {exc}", file=sys.stderr)
            time.sleep(5 * (n + 1))
    raise RuntimeError(f"could not fetch {source}: {last}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", default=DEFAULT_SOURCE, help="issue URL or saved HTML file")
    ap.add_argument("--index", default=str(DEFAULT_INDEX), help="landing page to rewrite")
    args = ap.parse_args(argv)
    try:
        data = parse_issue(fetch(args.source))
        index = Path(args.index)
        index.write_text(bake(index.read_text(encoding="utf-8"), data), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - any failure must leave index.html untouched
        print(f"bake_landing_excerpt: FAILED, index.html left as is: {exc}", file=sys.stderr)
        return 1
    print(f"baked {data['date']}: {data['paper']['title'][:60]!r} + {len(data['stories'])} stories")
    return 0


if __name__ == "__main__":
    sys.exit(main())
