# agents/render/engine.py
#
# The block loop: walk a newsletter definition's blocks in order and draw each one from the content given.
# Pure code, no Claude call and no Firestore: the same function draws the pre-rendered ready-made newsletters
# (agent3, via compose_html) and custom newsletters (render_from_editions, for the studio preview and sends).
#
# Single-topic emails keep the `<!-- SECTION:<name> -->` markers agent4's AI section reorder relies on. An email
# that mixes topics labels every section with its topic and marks it `<!-- SECTION:<topic>:<name> -->`; custom
# sections are always marked `<!-- SECTION:custom:<title> -->`.

import os
from dataclasses import dataclass, field
from typing import Callable

import topics
from newsletter_def import CustomSectionBlock, Definition, IntroBlock, SectionBlock, ShareBlock, WidgetBlock

from . import blocks, shell
from .palette import AI_LINK, darken
from .text import esc

DEFAULT_ICON = "📌"
CUSTOM_ICON = "🔭"
CUSTOM_LABEL = "Custom"
TOC_LABEL_MAX = 18

# Short topic names for the section labels of mixed emails. The profiles only have the long newsletter name;
# a topic missing here falls back to it. (Follow-up: a `short_name` in topics/<id>.yaml.)
TOPIC_LABELS = {"ai": "AI", "finance": "Finance", "gaming": "Video games", "boardgames": "Board games",
                "printing3d": "3D printing"}


@dataclass
class Content:
    """What the blocks are drawn from. Articles are in the card shape (title, url, summary, hn_score...).
    `intro` None means a templated intro line built from what the email holds (custom newsletters)."""
    intro: str | None = ""
    sections: dict = field(default_factory=dict)   # {(topic id, section name): [article, ...]}
    custom: dict = field(default_factory=dict)     # {custom section title: [article, ...]}
    widgets: dict = field(default_factory=dict)    # {widget id: data}


@dataclass
class Look:
    """Masthead and footer of one newsletter. Text here is raw; the renderer escapes it.
    `disclaimers` None means one line per topic the email draws on (each topic's profile disclaimer, once)."""
    name: str
    tagline: str
    accent: str
    link_color: str
    week_of: str
    logo: bool = False
    empty_text: str = "Nothing notable this week."
    disclaimers: tuple | None = ()
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

    @classmethod
    def for_definition(cls, definition: Definition, week_of: str) -> "Look":
        """A custom newsletter: its own name and accent, no brand logo, the templated intro's label."""
        return cls(name=definition.name, tagline="Your weekly briefing", accent=definition.accent_color,
                   link_color=darken(definition.accent_color), week_of=week_of, disclaimers=None,
                   intro_label="THIS WEEK")


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


def _topic(topic_id: str):
    try:
        return topics.get_topic(topic_id)
    except topics.TopicError:
        return None


def _section_active(block: SectionBlock, prefs: dict | None) -> bool:
    """Variant flags (the AI Canada section) apply only when the caller passes the reader's prefs."""
    topic = _topic(block.topic)
    return prefs is None or topic is None or topic.category_active(block.section, prefs)


def topic_label(topic_id: str) -> str:
    topic = _topic(topic_id)
    return TOPIC_LABELS.get(topic_id) or (topic.name if topic else topic_id)


def _toc_label(name: str) -> str:
    return name.split(" &")[0].split(",")[0].upper()


def _cards(articles: list, look: Look) -> str:
    return "".join(
        blocks.render_article_card(a, is_last=(j == len(articles) - 1), accent=look.accent, link_color=look.link_color)
        for j, a in enumerate(articles)
    )


def _join(parts: list[str]) -> str:
    if len(parts) <= 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def templated_intro(shown: list[tuple[str, str, int]]) -> str:
    """One line from what the email holds, no Claude call: [(kind, name, count), ...] in email order, kind being
    "section" or "custom". E.g. "This week: 3 stories in Crypto & Fintech, 4 in Releases & Reviews, and your
    custom section on SpaceX." """
    parts, first = [], True
    for kind, name, n in shown:
        if n <= 0:
            continue
        if kind == "custom":
            parts.append(f"your custom section on {name}")
        elif first:
            parts.append(f"{n} {'story' if n == 1 else 'stories'} in {name}")
            first = False
        else:
            parts.append(f"{n} in {name}")
    if not parts:
        return "Your newsletter for this week."
    return f"This week: {_join(parts)}."


def render(definition: Definition, content: Content, look: Look, prefs: dict | None = None,
           render_widget: Callable = default_render_widget) -> RenderResult:
    accent = look.accent
    mixed = definition.is_mixed()
    warnings: list = []
    ctx = RenderContext(definition, content, look, prefs, warnings)

    has_intro = False
    lead: list[str] = []      # widgets above the first section (the paper spotlight)
    rest: list[str] = []
    toc: list[tuple[str, str]] = []
    shown: list[tuple[str, str, int]] = []   # for the templated intro
    used_topics: list[str] = []
    number = 0

    def use(topic_id):
        if topic_id and topic_id not in used_topics:
            used_topics.append(topic_id)

    for block in definition.blocks:
        fragment = ""
        if isinstance(block, IntroBlock):
            has_intro = True
            continue
        if isinstance(block, WidgetBlock):
            fragment = render_widget(block, ctx)
            if fragment:
                use(block.topic)
                if block.widget in WIDGET_TOC:
                    toc.append(WIDGET_TOC[block.widget])
            if not rest:
                lead.append(fragment)
                continue
        elif isinstance(block, SectionBlock):
            if not _section_active(block, prefs):
                continue
            articles = content.sections.get((block.topic, block.section), [])[:block.max_items]
            if not articles and block.if_empty == "hide":
                continue
            number += 1
            topic = _topic(block.topic)
            c = topic.category(block.section) if topic else None
            body = _cards(articles, look) if articles else blocks.empty_note(look.empty_text)
            fragment = blocks.section_rows(
                block.section, f"{number:02d}", c.icon if c else DEFAULT_ICON, body, accent,
                marker=f"{block.topic}:{block.section}" if mixed else None,
                topic_label=esc(topic_label(block.topic)) if mixed else None,
                topic_accent=topic.accent_color if (mixed and topic) else None)
            toc.append((f"{number:02d}", _toc_label(block.section)))
            shown.append(("section", block.section, len(articles)))
            use(block.topic)
        elif isinstance(block, CustomSectionBlock):
            if block.title not in content.custom:
                warnings.append(f"custom section {block.title!r} left out: no articles for it this week")
                continue
            articles = content.custom[block.title][:block.max_items]
            number += 1
            title = esc(block.title)
            body = _cards(articles, look) if articles else blocks.empty_note(look.empty_text)
            fragment = blocks.section_rows(
                title, f"{number:02d}", CUSTOM_ICON, body, accent, marker=f"custom:{title}",
                topic_label=CUSTOM_LABEL if mixed else None)
            toc.append((f"{number:02d}", esc(_toc_label(block.title)[:TOC_LABEL_MAX])))
            shown.append(("custom", block.title, len(articles)))
        elif isinstance(block, ShareBlock):
            fragment = blocks.share_strip()
        rest.append(fragment)

    intro = content.intro if content.intro is not None else templated_intro(shown)
    intro_html = shell.intro_row(intro, esc(look.week_of), look.intro_label) if has_intro else ""

    if look.disclaimers is None:
        lines = []
        for topic_id in used_topics:
            topic = _topic(topic_id)
            if topic and topic.disclaimer and topic.disclaimer not in lines:
                lines.append(topic.disclaimer)
    else:
        lines = list(look.disclaimers)

    name = esc(look.name)
    toc_row1, toc_row2 = shell.toc_rows(toc, accent)
    logo = shell.logo_img(name) if look.logo else ""
    address = shell.address_html(os.environ.get("MAILING_ADDRESS", "").strip(), accent)
    disclaimers = "".join(shell.disclaimer_html(d) for d in lines)

    inner = (
        shell.masthead(name, esc(look.tagline), esc(look.week_of), accent, logo, toc_row1, toc_row2)
        + "\n\n    " + intro_html
        + "\n\n    " + "".join(lead)
        + "\n    " + "".join(rest)
        + "\n\n    " + shell.footer(name, accent, address, disclaimers)
    )
    html = shell.page(name, intro, inner)
    return RenderResult(html=html, size_bytes=len(html.encode("utf-8")), warnings=warnings)
