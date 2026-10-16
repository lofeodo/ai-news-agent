"""The shipped topic profiles (topics/*.yaml): they load, their prompts format, and they are distinct."""
import pytest

import topics
from filter_tool import build_filter_tool

NEW_TOPICS = ["finance", "gaming", "boardgames", "printing3d"]
ALL = ["ai"] + NEW_TOPICS


def test_the_expected_profiles_exist():
    assert topics.list_topic_ids() == sorted(ALL)


@pytest.mark.parametrize("tid", ALL)
def test_profile_loads(tid):
    t = topics.get_topic(tid)
    assert t.id == tid and 1 <= len(t.categories) <= topics.MAX_CATEGORIES


@pytest.mark.parametrize("tid", NEW_TOPICS)
def test_new_topics_are_english_single_variant_without_spotlight(tid):
    t = topics.get_topic(tid)
    assert t.languages == ("en",) and t.variants == ("0_0",) and t.spotlight == "none"
    assert t.max_articles > 0                                   # a feed-heavy topic must be capped
    assert all(c.variant_flag is None for c in t.categories)    # nothing is hidden in a one-variant topic


@pytest.mark.parametrize("tid", NEW_TOPICS)
def test_new_topics_use_the_generic_prompts(tid):
    assert topics.get_topic(tid).prompts == {}                  # every prompt falls back to prompts/generic/


@pytest.mark.parametrize("tid", NEW_TOPICS)
def test_every_category_has_an_icon_and_a_sane_cap(tid):
    for c in topics.get_topic(tid).categories:
        assert c.icon != "📌", f"{c.name} has no icon"
        assert 1 <= c.cap <= 5, c.name


@pytest.mark.parametrize("tid", NEW_TOPICS)
def test_sources_are_usable(tid):
    t = topics.get_topic(tid)
    kinds = [s["type"] for s in t.sources]
    assert "rss" in kinds and "newsapi" in kinds
    urls = [f["url"] for s in t.sources if s["type"] == "rss" for f in s["feeds"]]
    assert len(urls) == len(set(urls)), "a feed is listed twice"
    assert all(u.startswith("https://") for u in urls)
    for s in t.sources:
        if s["type"] == "newsapi":
            assert all(len(q) <= 500 for q in s["queries"]), "NewsAPI rejects queries over 500 characters"


@pytest.mark.parametrize("tid", ALL)
def test_every_prompt_formats_with_the_topics_values(tid):
    """A stray brace in a definition, guideline or name must not break str.format() at run time."""
    t = topics.get_topic(tid)
    t.read_filled_prompt("news_filter").format(articles="A")
    t.read_filled_prompt("selection").format(category=t.categories[0].name, articles="A", cap=3, runners_up=6)
    t.read_filled_prompt("intro").format(date="d", papers="", headlines="h")
    t.read_filled_prompt("news_summary").format(title="t", text="x", style_instruction="")
    t.read_filled_prompt("news_summary_fallback").format(title="t", description="d", style_instruction="")


@pytest.mark.parametrize("tid", NEW_TOPICS)
def test_filled_prompts_carry_the_topic(tid):
    t = topics.get_topic(tid)
    flt = t.read_filled_prompt("news_filter").format(articles="A")
    assert t.name in flt and t.audience in flt
    for c in t.categories:
        assert f"- {c.name}: {c.definition}" in flt
    sel = t.read_filled_prompt("selection").format(category="X", articles="A", cap=3, runners_up=6)
    assert t.name in sel
    assert "{" not in sel.replace('{"selected"', "")


@pytest.mark.parametrize("tid", NEW_TOPICS)
def test_filter_tool_enum_is_the_topics_categories(tid):
    t = topics.get_topic(tid)
    enum = build_filter_tool(t.category_names)["input_schema"]["properties"]["articles"]["items"]["properties"]["category"]["enum"]
    assert enum == t.category_names


def test_topics_are_distinct():
    ts = [topics.get_topic(t) for t in ALL]
    for field in ("name", "tagline", "accent_color"):
        values = [getattr(t, field) for t in ts]
        assert len(set(values)) == len(values), f"two topics share a {field}"


def test_finance_carries_a_disclaimer_and_ai_does_not():
    assert "not" in topics.get_topic("finance").disclaimer.lower()
    assert topics.get_topic("ai").disclaimer == ""


def test_guidelines_reach_the_prompts_only_for_topics_that_set_them():
    gaming = topics.get_topic("gaming").read_filled_prompt("news_filter")
    assert "do not favour one" in gaming.replace("\n", " ")
    bare = topics.parse_topic({
        "id": "bare", "name": "Bare", "tagline": "t", "audience": "a", "accent_color": "#112233",
        "languages": ["en"], "variants": ["0_0"], "spotlight": "none",
        "categories": [{"name": "One", "definition": "d"}],
        "sources": [{"type": "rss", "feeds": [{"name": "f", "url": "https://e.com/f"}]}],
    })
    assert "No extra guidance." in bare.read_filled_prompt("news_filter")
