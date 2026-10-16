# agents/newsletter_def.py
#
# Newsletter definitions (contract C3 in docs/plans/newsletter-platform.md): how to *assemble* one email, as an
# ordered list of blocks (intro, sections drawn from any topic, widgets, custom sections, share strip) plus a
# name and an accent colour. Every ready-made newsletter has one derived from its topic profile (from_topic);
# a custom newsletter is a definition a premium member saved. The block renderer (agents/render/) draws them.

import re
from dataclasses import dataclass, field

import topics

VERSION = 1
MAX_BLOCKS = 12
MAX_SECTIONS = 8
MAX_CUSTOM_SECTIONS = 3
MAX_WIDGETS = 4
MIN_ITEMS, MAX_ITEMS = 1, 5
NAME_MAX = 60
QUERY_MAX = 200
IF_EMPTY = ("note", "hide")   # an empty section shows "Nothing notable this week." (note) or is left out (hide)

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

def default_widget_catalog() -> dict:
    """The widgets parse() knows when the caller passes none: the widget registry (agents/widgets/). Imported here
    because the widgets package imports the renderer, which imports this module."""
    from widgets import registry
    return registry.definitions_catalog()


class DefinitionError(ValueError):
    """A definition broke one or more rules; `errors` lists every one in plain words."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


@dataclass(frozen=True)
class IntroBlock:
    type: str = field(default="intro", init=False)


@dataclass(frozen=True)
class WidgetBlock:
    widget: str
    topic: str | None = None      # whose edition supplies the data; None for personal widgets
    config: dict = field(default_factory=dict)
    type: str = field(default="widget", init=False)


@dataclass(frozen=True)
class SectionBlock:
    topic: str
    section: str
    max_items: int
    if_empty: str = "note"
    type: str = field(default="section", init=False)


@dataclass(frozen=True)
class CustomSectionBlock:
    title: str
    query: str
    max_items: int
    type: str = field(default="custom_section", init=False)


@dataclass(frozen=True)
class ShareBlock:
    type: str = field(default="share", init=False)


@dataclass(frozen=True)
class Definition:
    name: str
    accent_color: str
    blocks: tuple
    version: int = VERSION

    def topic_ids(self) -> list[str]:
        """Distinct topics the sections and edition widgets draw from, in block order."""
        seen = []
        for b in self.blocks:
            t = getattr(b, "topic", None)
            if t and t not in seen:
                seen.append(t)
        return seen

    def is_mixed(self) -> bool:
        """True when the email draws on more than one source (each topic, and custom sections, count as one)."""
        sources = len(self.topic_ids()) + any(isinstance(b, CustomSectionBlock) for b in self.blocks)
        return sources > 1


_FIELDS = {
    "intro":          ({"type"}, set()),
    "share":          ({"type"}, set()),
    "widget":         ({"type", "widget"}, {"topic", "config"}),
    "section":        ({"type", "topic", "section", "max_items"}, {"if_empty"}),
    "custom_section": ({"type", "title", "query", "max_items"}, set()),
}


def _widget_attr(info, name: str, default):
    if isinstance(info, dict):
        return info.get(name, default)
    return getattr(info, name, default)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _text_ok(v, max_len: int) -> bool:
    return isinstance(v, str) and 1 <= len(v.strip()) <= max_len and "<" not in v and ">" not in v


def parse(raw, *, widgets=None, custom: bool = True, premium: bool = True) -> Definition:
    """Validate a raw definition and build a Definition, or raise DefinitionError listing every problem.

    `widgets` is the widget catalog: a set of ids or a mapping of id -> info with optional `scope`
    ("edition" | "personal"), `topics`, `premium` and `commercial_use` (dicts or objects). `custom` marks a
    member's own newsletter (the paid product, where a non-commercial widget is never allowed); `premium` says
    whether the owner may use premium widgets.
    """
    catalog = default_widget_catalog() if widgets is None else widgets
    if not isinstance(catalog, dict):
        catalog = {w: {} for w in catalog}
    errors: list[str] = []

    if not isinstance(raw, dict):
        raise DefinitionError(["the definition must be an object"])
    extra = set(raw) - {"version", "name", "accent_color", "blocks"}
    if extra:
        errors.append(f"unknown field(s): {', '.join(sorted(extra))}")
    if raw.get("version") != VERSION:
        errors.append(f"version must be {VERSION}")
    name = raw.get("name")
    if not _text_ok(name, NAME_MAX):
        errors.append(f"name must be 1 to {NAME_MAX} characters with no angle brackets")
    color = raw.get("accent_color")
    if not (isinstance(color, str) and _COLOR_RE.match(color)):
        errors.append("accent_color must look like #7fd1a0")

    raw_blocks = raw.get("blocks")
    if not isinstance(raw_blocks, list) or not raw_blocks:
        errors.append("blocks must be a non-empty list")
        raw_blocks = []
    elif len(raw_blocks) > MAX_BLOCKS:
        errors.append(f"at most {MAX_BLOCKS} blocks (got {len(raw_blocks)})")

    known_topics = set(topics.list_topic_ids())
    blocks = []
    for n, rb in enumerate(raw_blocks, start=1):
        where = f"block {n}"
        if not isinstance(rb, dict) or rb.get("type") not in _FIELDS:
            errors.append(f"{where}: unknown block type {rb.get('type') if isinstance(rb, dict) else rb!r}")
            continue
        kind = rb["type"]
        where = f"block {n} ({kind})"
        required, optional = _FIELDS[kind]
        missing, unknown = required - set(rb), set(rb) - required - optional
        if missing:
            errors.append(f"{where}: missing {', '.join(sorted(missing))}")
        if unknown:
            errors.append(f"{where}: unknown field(s) {', '.join(sorted(unknown))}")
        if missing:
            continue

        if kind in ("section", "custom_section"):
            mi = rb["max_items"]
            if not (_is_int(mi) and MIN_ITEMS <= mi <= MAX_ITEMS):
                errors.append(f"{where}: max_items must be a whole number from {MIN_ITEMS} to {MAX_ITEMS}")

        if kind == "intro":
            if n != 1:
                errors.append(f"{where}: the intro must be the first block")
            blocks.append(IntroBlock())
        elif kind == "share":
            blocks.append(ShareBlock())
        elif kind == "section":
            t, s = rb["topic"], rb["section"]
            if t not in known_topics:
                errors.append(f"{where}: unknown topic {t!r}")
            elif not (isinstance(s, str) and topics.get_topic(t).category(s)):
                errors.append(f"{where}: topic {t!r} has no section {s!r}")
            if_empty = rb.get("if_empty", "note")
            if if_empty not in IF_EMPTY:
                errors.append(f"{where}: if_empty must be one of {', '.join(IF_EMPTY)}")
            blocks.append(SectionBlock(t, s, rb["max_items"], if_empty))
        elif kind == "custom_section":
            if not _text_ok(rb["title"], NAME_MAX):
                errors.append(f"{where}: title must be 1 to {NAME_MAX} characters with no angle brackets")
            if not _text_ok(rb["query"], QUERY_MAX):
                errors.append(f"{where}: query must be 1 to {QUERY_MAX} characters with no angle brackets")
            blocks.append(CustomSectionBlock(rb["title"], rb["query"], rb["max_items"]))
        elif kind == "widget":
            errors.extend(_check_widget(where, rb, catalog, known_topics, custom, premium))
            config = rb.get("config", {})
            blocks.append(WidgetBlock(rb["widget"], rb.get("topic"), dict(config) if isinstance(config, dict) else {}))

    counts = {k: sum(1 for b in blocks if b.type == k) for k in _FIELDS}
    for kind, limit in (("section", MAX_SECTIONS), ("custom_section", MAX_CUSTOM_SECTIONS),
                        ("widget", MAX_WIDGETS), ("intro", 1), ("share", 1)):
        if counts[kind] > limit:
            errors.append(f"at most {limit} {kind.replace('_', ' ')} block{'s' if limit > 1 else ''} "
                          f"(got {counts[kind]})")

    if errors:
        raise DefinitionError(errors)
    return Definition(name=name.strip(), accent_color=color, blocks=tuple(blocks))


def _check_widget(where, rb, catalog, known_topics, custom, premium) -> list[str]:
    wid, topic, config = rb["widget"], rb.get("topic"), rb.get("config", {})
    if not isinstance(wid, str) or wid not in catalog:
        return [f"{where}: unknown widget {wid!r}"]
    info = catalog[wid]
    errors = []
    if not isinstance(config, dict):
        errors.append(f"{where}: config must be an object")
    scope = _widget_attr(info, "scope", "edition")
    if custom and _widget_attr(info, "commercial_use", True) is False:
        errors.append(f"{where}: widget {wid!r} cannot be used in a custom newsletter")
    if (scope == "personal" or _widget_attr(info, "premium", False)) and not premium:
        errors.append(f"{where}: widget {wid!r} needs a premium membership")
    if scope == "personal":
        if topic is not None:
            errors.append(f"{where}: a personal widget takes no topic")
    elif topic not in known_topics:
        errors.append(f"{where}: an edition widget needs the topic whose edition supplies it")
    else:
        allowed = tuple(_widget_attr(info, "topics", ("*",)) or ("*",))
        if "*" not in allowed and topic not in allowed:
            errors.append(f"{where}: widget {wid!r} is not available for topic {topic!r}")
    return errors


def to_dict(definition: Definition) -> dict:
    """The JSON form (C3). parse(to_dict(d)) == d."""
    blocks = []
    for b in definition.blocks:
        if isinstance(b, SectionBlock):
            d = {"type": "section", "topic": b.topic, "section": b.section, "max_items": b.max_items}
            if b.if_empty != "note":
                d["if_empty"] = b.if_empty
        elif isinstance(b, CustomSectionBlock):
            d = {"type": "custom_section", "title": b.title, "query": b.query, "max_items": b.max_items}
        elif isinstance(b, WidgetBlock):
            d = {"type": "widget", "widget": b.widget}
            if b.topic is not None:
                d["topic"] = b.topic
            d["config"] = dict(b.config)
        else:
            d = {"type": b.type}
        blocks.append(d)
    return {"version": definition.version, "name": definition.name,
            "accent_color": definition.accent_color, "blocks": blocks}


def from_topic(topic) -> Definition:
    """A ready-made newsletter's definition: intro, the profile's widgets placed after_intro then
    before_sections (the AI paper spotlight), every category in profile order with its cap, the share strip after
    the first section (AI only, as today), then the after_sections widgets. Widgets placed "none" are produced
    for the edition but not shown here."""
    def widget_blocks(placement):
        return [WidgetBlock(w.id, topic.id, dict(w.config)) for w in topic.widgets if w.placement == placement]

    blocks = [IntroBlock()] + widget_blocks("after_intro") + widget_blocks("before_sections")
    for i, c in enumerate(topic.categories):
        blocks.append(SectionBlock(topic.id, c.name, c.cap))
        if i == 0 and topic.is_default:
            blocks.append(ShareBlock())
    blocks += widget_blocks("after_sections")
    return Definition(name=topic.name, accent_color=topic.accent_color, blocks=tuple(blocks))
