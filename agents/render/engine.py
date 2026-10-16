# agents/render/engine.py
#
# The block loop: walk a newsletter definition's blocks in order and draw each one from the content given.
# Pure code, no Claude call and no Firestore: the same function draws the pre-rendered ready-made newsletters
# (agent3, via compose_html) and, later, custom newsletters at preview and send time.

import os
from dataclasses import dataclass, field
from typing import Callable

import topics
from newsletter_def import CustomSectionBlock, Definition, IntroBlock, SectionBlock, ShareBlock, WidgetBlock

from . import blocks, shell
from .palette import AI_LINK, darken
from .text import esc

DEFAULT_ICON = "📌"


@dataclass
class Content:
    """What the blocks are drawn from. Articles are in the card shape (title, url, summary, hn_score...)."""
    intro: str = ""
    sections: dict = field(default_factory=dict)   # {(topic id, section name): [article, ...]}
    custom: dict = field(default_factory=dict)     # {custom section title: [article, ...]}
    widgets: dict = field(default_factory=dict)    # {widget id: data}


@dataclass
class Look:
    """Masthead and footer of one newsletter. Text here is raw; the renderer escapes it."""
    name: str
    tagline: str
    accent: str
    link_color: str
    week_of: str
    logo: bool = False
    empty_text: str = "Nothing notable this week."
    disclaimers: tuple = ()
    intro_label: str = "EDITOR&apos;S NOTE"   # HTML, not escaped

    @classmethod
    def from_topic(cls, topic, week_of: str) -> "Look":
        return cls(
            name=topic.name, tagline=topic.tagline, accent=topic.accent_color,
            link_color=AI_LINK if topic.is_default else darken(topic.accent_color),
            week_of=week_of, logo=topic.is_default,
            empty_text="No notable releases this week." if topic.is_default else "Nothing notable this week.",
            disclaimers=(topic.disclaimer,) if topic.disclaimer else (),
        )


@dataclass
class RenderResult:
    html: str
    size_bytes: int
    warnings: list = field(default_factory=list)


@dataclass
class RenderContext:
    """Passed to the widget plug-in."""
    definition: Definition
    content: Content
    look: Look
    prefs: dict | None
    warnings: list


# Widgets that get a table-of-contents entry, and its label. The paper leads the contents, unnumbered.
WIDGET_TOC = {"paper_spotlight": ("RES", "RESEARCH")}


def default_render_widget(block: WidgetBlock, ctx: RenderContext) -> str:
    """The widget plug-in until roadmap C's registry replaces it: knows only the paper spotlight.
    Returns "" (the block is left out) for anything else."""
    if block.widget == "paper_spotlight":
        data = ctx.content.widgets.get("paper_spotlight") or {}
        return blocks.research_rows(data.get("papers") or [])
    ctx.warnings.append(f"widget {block.widget!r} left out: no renderer for it")
    return ""


def _section_active(block: SectionBlock, prefs: dict | None) -> bool:
    """Variant flags (the AI Canada section) apply only when the caller passes the reader's prefs."""
    if prefs is None:
        return True
    try:
        topic = topics.get_topic(block.topic)
    except topics.TopicError:
        return True
    return topic.category_active(block.section, prefs)


def _section_icon(block: SectionBlock) -> str:
    try:
        c = topics.get_topic(block.topic).category(block.section)
    except topics.TopicError:
        c = None
    return c.icon if c else DEFAULT_ICON


def _toc_label(name: str) -> str:
    return name.split(" &")[0].split(",")[0].upper()


def _cards(articles: list, look: Look) -> str:
    return "".join(
        blocks.render_article_card(a, is_last=(j == len(articles) - 1), accent=look.accent, link_color=look.link_color)
        for j, a in enumerate(articles)
    )


def render(definition: Definition, content: Content, look: Look, prefs: dict | None = None,
           render_widget: Callable = default_render_widget) -> RenderResult:
    accent = look.accent
    warnings: list = []
    ctx = RenderContext(definition, content, look, prefs, warnings)

    intro_html = ""
    lead: list[str] = []      # widgets above the first section (the paper spotlight)
    rest: list[str] = []
    toc: list[tuple[str, str]] = []
    number = 0

    for block in definition.blocks:
        fragment = ""
        if isinstance(block, IntroBlock):
            intro_html = shell.intro_row(content.intro, look.week_of, look.intro_label)
            continue
        if isinstance(block, WidgetBlock):
            fragment = render_widget(block, ctx)
            if fragment and block.widget in WIDGET_TOC:
                toc.append(WIDGET_TOC[block.widget])
            if not rest:
                lead.append(fragment)
                continue
        elif isinstance(block, SectionBlock):
            if not _section_active(block, prefs):
                continue
            articles = content.sections.get((block.topic, block.section), [])[:block.max_items]
            number += 1
            body = _cards(articles, look) if articles else blocks.empty_note(look.empty_text)
            fragment = blocks.section_rows(block.section, f"{number:02d}", _section_icon(block), body, accent)
            toc.append((f"{number:02d}", _toc_label(block.section)))
        elif isinstance(block, CustomSectionBlock):
            warnings.append(f"custom section {block.title!r} left out: not supported yet")
        elif isinstance(block, ShareBlock):
            fragment = blocks.share_strip()
        rest.append(fragment)

    name = esc(look.name)
    toc_row1, toc_row2 = shell.toc_rows(toc, accent)
    logo = shell.logo_img(name) if look.logo else ""
    address = shell.address_html(os.environ.get("MAILING_ADDRESS", "").strip(), accent)
    disclaimers = "".join(shell.disclaimer_html(d) for d in look.disclaimers)

    inner = (
        shell.masthead(name, esc(look.tagline), look.week_of, accent, logo, toc_row1, toc_row2)
        + "\n\n    " + intro_html
        + "\n\n    " + "".join(lead)
        + "\n    " + "".join(rest)
        + "\n\n    " + shell.footer(name, accent, address, disclaimers)
    )
    html = shell.page(name, content.intro, inner)
    return RenderResult(html=html, size_bytes=len(html.encode("utf-8")), warnings=warnings)
