"""Newsletter definitions (contract C3): validation rules, round trip and the ready-made definitions."""
import copy

import pytest

import newsletter_def as nd
import topics

VALID = {
    "version": 1,
    "name": "My Monday brief",
    "accent_color": "#7fd1a0",
    "blocks": [
        {"type": "intro"},
        {"type": "widget", "widget": "paper_spotlight", "topic": "ai", "config": {}},
        {"type": "section", "topic": "finance", "section": "Crypto & Fintech", "max_items": 3},
        {"type": "section", "topic": "gaming", "section": "Releases & Reviews", "max_items": 4},
        {"type": "custom_section", "title": "SpaceX", "query": "SpaceX product launches & mission updates",
         "max_items": 3},
        {"type": "share"},
    ],
}

WIDGETS = {
    "paper_spotlight": {"scope": "edition", "topics": ("ai",)},
    "market_snapshot": {"scope": "edition", "topics": ("finance",)},
    "watchlist": {"scope": "personal", "premium": True},
    "bgg_hot": {"scope": "edition", "topics": ("boardgames",), "commercial_use": False},
    "anywhere": {"scope": "edition", "topics": ("*",)},
}


def _with(**changes):
    d = copy.deepcopy(VALID)
    d.update(changes)
    return d


def _blocks(*blocks):
    return _with(blocks=list(blocks))


def _errors(raw, **kw) -> list[str]:
    with pytest.raises(nd.DefinitionError) as exc:
        nd.parse(raw, **kw)
    return exc.value.errors


def _sec(topic="finance", section="Crypto & Fintech", n=3, **extra):
    return {"type": "section", "topic": topic, "section": section, "max_items": n, **extra}


def _custom(title="SpaceX", n=2):
    return {"type": "custom_section", "title": title, "query": f"{title} news", "max_items": n}


def test_the_contract_example_parses():
    d = nd.parse(VALID)
    assert d.name == "My Monday brief" and d.accent_color == "#7fd1a0"
    assert [b.type for b in d.blocks] == ["intro", "widget", "section", "section", "custom_section", "share"]
    assert d.topic_ids() == ["ai", "finance", "gaming"] and d.is_mixed()


def test_round_trip():
    d = nd.parse(VALID)
    assert nd.to_dict(d) == VALID
    assert nd.parse(nd.to_dict(d)) == d
    hide = nd.parse(_blocks(_sec(if_empty="hide")))
    assert nd.to_dict(hide)["blocks"][0]["if_empty"] == "hide" and nd.parse(nd.to_dict(hide)) == hide


@pytest.mark.parametrize("raw, fragment", [
    ("not a dict", "must be an object"),
    (_with(version=2), "version must be 1"),
    (_with(extra=1), "unknown field(s): extra"),
    (_with(name=""), "name must be 1 to 60"),
    (_with(name="   "), "name must be 1 to 60"),
    (_with(name="x" * 61), "name must be 1 to 60"),
    (_with(name="<b>hi</b>"), "angle brackets"),
    (_with(accent_color="#7fd1a"), "accent_color"),
    (_with(accent_color="red"), "accent_color"),
    (_with(blocks=[]), "non-empty list"),
    (_with(blocks="intro"), "non-empty list"),
    (_blocks({"type": "poll"}), "unknown block type 'poll'"),
    (_blocks({"type": "intro", "text": "hi"}), "unknown field(s) text"),
    (_blocks({"type": "section", "topic": "finance"}), "missing max_items, section"),
    (_blocks(_sec(n=0)), "max_items must be a whole number from 1 to 5"),
    (_blocks(_sec(n=6)), "max_items must be a whole number from 1 to 5"),
    (_blocks(_sec(n=True)), "max_items must be a whole number"),
    (_blocks(_sec(n="3")), "max_items must be a whole number"),
    (_blocks(_sec(topic="cooking")), "unknown topic 'cooking'"),
    (_blocks(_sec(section="Weather")), "has no section 'Weather'"),
    (_blocks(_sec(if_empty="skip")), "if_empty must be one of note, hide"),
    (_blocks(_sec(), {"type": "intro"}), "the intro must be the first block"),
    (_blocks({"type": "intro"}, {"type": "intro"}), "at most 1 intro block"),
    (_blocks({"type": "share"}, {"type": "share"}), "at most 1 share block"),
    (_blocks(*[_sec()] * 9), "at most 8 section blocks"),
    (_blocks(*[_custom()] * 4), "at most 3 custom section blocks"),
    (_blocks(*[{"type": "share"}] * 13), "at most 12 blocks"),
    (_blocks(_custom(title="<script>")), "title must be 1 to 60"),
    (_blocks({"type": "custom_section", "title": "A", "query": "", "max_items": 2}), "query must be 1 to 200"),
    (_blocks({"type": "custom_section", "title": "A", "query": "q" * 201, "max_items": 2}), "query must be 1 to 200"),
    (_blocks({"type": "widget", "widget": "nope", "topic": "ai"}), "unknown widget 'nope'"),
    (_blocks({"type": "widget", "widget": "paper_spotlight"}), "needs the topic"),
    (_blocks({"type": "widget", "widget": "paper_spotlight", "topic": "finance"}), "not available for topic"),
    (_blocks({"type": "widget", "widget": "paper_spotlight", "topic": "ai", "config": []}), "config must be"),
])
def test_rules(raw, fragment):
    errors = _errors(raw)
    assert any(fragment in e for e in errors), errors


def test_every_error_is_reported_at_once():
    errors = _errors(_with(name="", accent_color="x", blocks=[_sec(n=9), {"type": "poll"}]))
    assert len(errors) == 4


def test_section_names_may_repeat_across_topics():
    raw = _blocks(_sec("finance", "Canada"), _sec("ai", "Canada & Montreal"), _sec("finance", "Canada"))
    assert len(nd.parse(raw).blocks) == 3


def test_widget_catalog_is_injectable():
    four = [{"type": "widget", "widget": "anywhere", "topic": t} for t in ("ai", "finance", "gaming", "boardgames")]
    assert len(nd.parse(_blocks(*four), widgets=WIDGETS).blocks) == 4
    errors = _errors(_blocks(*four, {"type": "widget", "widget": "anywhere", "topic": "ai"}), widgets=WIDGETS)
    assert any("at most 4 widget blocks" in e for e in errors)
    assert nd.parse(_blocks({"type": "widget", "widget": "market_snapshot", "topic": "finance"}), widgets=WIDGETS)
    assert "market_snapshot" in _errors(_blocks({"type": "widget", "widget": "market_snapshot", "topic": "finance"}))[0]
    # A plain set of ids works too.
    assert nd.parse(_blocks({"type": "widget", "widget": "x", "topic": "gaming"}), widgets={"x"})


def test_personal_widgets_need_premium_and_no_topic():
    watch = {"type": "widget", "widget": "watchlist", "config": {"tickers": ["SHOP"]}}
    d = nd.parse(_blocks(watch), widgets=WIDGETS)
    assert d.blocks[0].topic is None and d.blocks[0].config == {"tickers": ["SHOP"]}
    assert any("premium" in e for e in _errors(_blocks(watch), widgets=WIDGETS, premium=False))
    assert any("takes no topic" in e for e in _errors(_blocks({**watch, "topic": "finance"}), widgets=WIDGETS))


def test_non_commercial_widgets_only_outside_custom_newsletters():
    bgg = {"type": "widget", "widget": "bgg_hot", "topic": "boardgames"}
    assert any("cannot be used in a custom newsletter" in e for e in _errors(_blocks(bgg), widgets=WIDGETS))
    assert nd.parse(_blocks(bgg), widgets=WIDGETS, custom=False)


@pytest.mark.parametrize("topic_id", ["ai", "finance", "gaming", "boardgames", "printing3d"])
def test_from_topic_is_valid_and_follows_the_profile(topic_id):
    topic = topics.get_topic(topic_id)
    d = nd.from_topic(topic)
    assert nd.parse(nd.to_dict(d), custom=False) == d
    assert d.name == topic.name and d.accent_color == topic.accent_color
    assert d.blocks[0].type == "intro"
    sections = [b for b in d.blocks if b.type == "section"]
    assert [(b.section, b.max_items) for b in sections] == [(c.name, c.cap) for c in topic.categories]
    assert all(b.topic == topic_id and b.if_empty == "note" for b in sections)
    assert not d.is_mixed()


def test_from_topic_ai_has_the_paper_first_and_share_after_the_first_section():
    types = [b.type for b in nd.from_topic(topics.get_topic("ai")).blocks]
    assert types[:5] == ["intro", "widget", "section", "share", "section"]
    assert types.count("share") == 1 and types.count("widget") == 1


def test_from_topic_without_spotlight_has_no_widget_or_share():
    types = [b.type for b in nd.from_topic(topics.get_topic("finance")).blocks]
    assert "widget" not in types and "share" not in types


def test_custom_sections_alone_or_with_one_topic():
    one = nd.parse(_blocks(_sec(), _sec(section="Canada")))
    assert not one.is_mixed()
    assert nd.parse(_blocks(_sec(), _custom())).is_mixed()
