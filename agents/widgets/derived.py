# agents/widgets/derived.py
#
# Shared by the widgets derived from the edition's picks (number_of_week, crowdfunding_watch): one Haiku call per
# edition, forced tool use for a structured answer, the shared prompt-injection guard, and usage logged on the
# context. Model output is data, never markup: every widget checks it against the articles it was shown and
# takes links only from those articles, never from the model.

import os
import re

import config
from prompt_guard import GUARD_XML_ARTICLES, neutralize_tags

from .base import WidgetSkip

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "prompts", "widgets")
SUMMARY_CHARS = 600
MAX_TOKENS = 600


def read_prompt(name: str) -> str:
    with open(os.path.join(PROMPTS_DIR, name), encoding="utf-8") as f:
        return f.read()


def collect(picks: dict, sections=None, limit: int = 30) -> list[dict]:
    """The picked articles (title, summary, url) of the named sections (all when None), deduplicated by url."""
    out, seen = [], set()
    for name, arts in (picks or {}).items():
        if sections is not None and name not in sections:
            continue
        for a in arts or []:
            if not isinstance(a, dict) or not a.get("url") or a["url"] in seen:
                continue
            seen.add(a["url"])
            out.append({"title": str(a.get("title") or ""), "summary": str(a.get("summary") or ""),
                        "url": a["url"], "section": name})
            if len(out) >= limit:
                return out
    return out


def format_articles(articles: list[dict]) -> str:
    return "\n".join(
        f"<article_{i}>\n[{i}] {neutralize_tags(a['title'])}\n"
        f"    Summary: {neutralize_tags(a['summary'][:SUMMARY_CHARS])}\n</article_{i}>"
        for i, a in enumerate(articles))


def call_tool(ctx, widget_id: str, prompt: str, tool: dict) -> dict:
    """One model call that must answer through `tool`; returns the tool input (a dict)."""
    if ctx.client is None:
        raise WidgetSkip("no model client")
    response = ctx.client.messages.create(
        model=config.SCORING_MODEL, max_tokens=MAX_TOKENS, system=GUARD_XML_ARTICLES,
        tools=[tool], tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": prompt}])
    ctx.log_usage(widget_id, response)
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool["name"]:
            return block.input if isinstance(block.input, dict) else {}
    raise ValueError("the model did not call the tool")


def norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def clean_text(text, limit: int) -> str:
    """Plain one-line text from model output: no tags, no control characters, at most `limit` characters."""
    text = re.sub(r"<[^>]*>", "", str(text or ""))
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]
