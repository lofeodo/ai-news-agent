# agents/render/from_editions.py
#
# Custom newsletters: assemble one email from this week's editions (C2), custom pools (C5) and widget data.
# Pure function, no Firestore and no Claude call: the caller (the studio preview, agent4 at send time) loads
# the inputs. Anything missing drops its block with a warning instead of failing the email.

import dataclasses
from datetime import date

from newsletter_def import CustomSectionBlock, Definition, SectionBlock, WidgetBlock

from .engine import Content, Look, RenderResult, default_render_widget, render

SIZE_WARNING_BYTES = 90_000   # Gmail clips an email at about 102 KB of HTML


def _card(a: dict) -> dict:
    """C2/C5 article -> the article card's shape."""
    return {"title": a.get("title"), "url": a.get("url"), "summary": a.get("summary"),
            "hn_score": a.get("points") if a.get("points_source") == "hn" else None}


def _fill(picks: list, runners_up: list, max_items: int, english_only: bool) -> list[dict]:
    out, seen = [], set()
    for a in list(picks or []) + list(runners_up or []):
        if len(out) >= max_items:
            break
        if not a or a.get("url") in seen or (english_only and a.get("language") == "fr"):
            continue
        seen.add(a.get("url"))
        out.append(_card(a))
    return out


def _week_label(editions_by_topic: dict) -> str:
    weeks = sorted(e.get("week_of") for e in editions_by_topic.values() if e and e.get("week_of"))
    try:
        d = date.fromisoformat(weeks[-1]) if weeks else date.today()
    except ValueError:
        d = date.today()
    return d.strftime("%B %d, %Y")


def render_from_editions(definition: Definition, editions_by_topic: dict, pools_by_query: dict | None = None,
                         widget_data: dict | None = None, prefs: dict | None = None, week_of: str | None = None,
                         render_widget=default_render_widget) -> RenderResult:
    """Render a custom newsletter.

    `editions_by_topic` maps topic id -> edition doc; `pools_by_query` maps a custom section's `query` -> its C5
    pool; `widget_data` maps widget id -> data. A section takes `picks` (or `picks_en` for an English-only
    reader, the default unless prefs["include_french"]) then `runners_up`, up to its max_items.
    """
    pools_by_query = pools_by_query or {}
    widget_data = widget_data or {}
    english_only = not (prefs or {}).get("include_french", False)
    warnings: list[str] = []
    kept, sections, custom = [], {}, {}

    for block in definition.blocks:
        if isinstance(block, SectionBlock):
            edition = editions_by_topic.get(block.topic)
            if not edition:
                warnings.append(f"section {block.topic}/{block.section!r} left out: no edition of {block.topic!r}")
                continue
            sec = (edition.get("sections") or {}).get(block.section)
            if sec is None:
                warnings.append(f"section {block.topic}/{block.section!r} left out: not in this week's edition")
                continue
            picks = sec.get("picks_en", sec.get("picks")) if english_only else sec.get("picks")
            sections[(block.topic, block.section)] = _fill(picks, sec.get("runners_up"), block.max_items,
                                                           english_only)
        elif isinstance(block, CustomSectionBlock):
            pool = pools_by_query.get(block.query)
            if not pool:
                warnings.append(f"custom section {block.title!r} left out: no articles for it this week")
                continue
            custom[block.title] = _fill(pool.get("picks"), pool.get("runners_up"), block.max_items, english_only)
        elif isinstance(block, WidgetBlock):
            if block.widget not in widget_data:
                warnings.append(f"widget {block.widget!r} left out: no data for it this week")
                continue
        kept.append(block)

    content = Content(intro=None, sections=sections, custom=custom, widgets=dict(widget_data))
    filtered = dataclasses.replace(definition, blocks=tuple(kept))
    look = Look.for_definition(definition, week_of or _week_label(editions_by_topic))
    result = render(filtered, content, look, prefs=None, render_widget=render_widget)
    warnings += result.warnings
    if result.size_bytes > SIZE_WARNING_BYTES:
        warnings.append(f"this email is {result.size_bytes / 1000:.0f} KB; Gmail cuts off emails over about "
                        f"102 KB, so remove a section or lower max_items")
    return RenderResult(html=result.html, size_bytes=result.size_bytes, warnings=warnings)
