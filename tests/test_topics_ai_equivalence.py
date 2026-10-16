"""The AI profile must reproduce the pre-topic constants exactly.

Every later step makes the pipeline read topics/ai.yaml instead of module constants. If this file
passes, the AI newsletter is configured the same as before; if you change one side, change both.
"""
import agent3_compose
import agent_subscriptions
import config
import filter_tool
import topics


def _ai():
    return topics.get_topic("ai")


def test_newsapi_queries_match():
    queries = []
    for src in _ai().sources:
        if src["type"] == "newsapi":
            queries += src["queries"]
    assert queries == config.NEWSAPI_QUERIES


def test_hn_source_is_the_plain_weekly_top():
    hn = [s for s in _ai().sources if s["type"] == "hn"]
    assert hn == [{"type": "hn"}]   # no query: today's top-1000-by-points call


def test_source_order_is_hn_then_newsapi():
    assert [s["type"] for s in _ai().sources] == ["hn", "newsapi"]


def test_section_order_matches_newsletter():
    assert _ai().category_names == agent3_compose.NEWS_CATEGORIES


def test_categories_match_filter_tool_enum():
    assert sorted(_ai().category_names) == sorted(filter_tool.CATEGORIES)


def test_caps_match():
    for c in _ai().categories:
        assert c.cap == config.section_cap(c.name), c.name


def test_icons_are_the_original_ones():
    """compose_html used to hold this table itself; it now reads the profile, so pin the values here."""
    original = {
        "Model & Product Releases": "🚀",
        "Industry & Business":      "💼",
        "Policy, Law & Regulation": "⚖️",
        "Open Source & Tools":      "🛠️",
        "Safety & Alignment":       "🛡️",
        "Society & Culture":        "🌍",
        "Canada & Montreal":        "🍁",
    }
    assert {c.name: c.icon for c in _ai().categories} == original


def test_accent_color_is_the_original_brand_amber():
    assert _ai().accent_color == agent3_compose._AMBER


def test_definitions_are_the_ones_in_the_filter_prompt():
    prompt = open("prompts/news_filter_prompt.txt", encoding="utf-8").read()
    for c in _ai().categories:
        assert f"- {c.name}: {c.definition}\n" in prompt, c.name


def test_default_sections_are_the_ai_sections():
    expected = [n for n in _ai().category_names if n != "Canada & Montreal"] + ["Research Spotlights"]
    assert sorted(agent_subscriptions.DEFAULT_SECTIONS) == sorted(expected)


def test_variants_match():
    assert agent3_compose.NEWSLETTER_VARIANTS == _ai().variant_prefs()


def test_branding_matches():
    assert _ai().name == agent3_compose.NEWSLETTER_NAME
    assert _ai().tagline == "Weekly AI Intelligence Dispatch"   # the header line under the title in the email


def test_ai_keeps_its_prompt_files():
    expected = {
        "news_filter": "prompts/news_filter_prompt.txt",
        "selection": "prompts/article_selection_prompt.txt",
        "intro": "prompts/intro_prompt.txt",
        "news_summary": "prompts/news_summary_prompt.txt",
        "news_summary_fallback": "prompts/news_summary_fallback_prompt.txt",
    }
    assert _ai().prompts == expected
    for key, path in expected.items():
        assert _ai().prompt_path(key) == path


def test_ai_has_no_article_cap_and_no_extra_paywalls():
    assert _ai().max_articles == 0
    assert _ai().paywalled_domains == ()


def test_spotlight_and_languages():
    assert _ai().spotlight == "papers"
    assert set(_ai().languages) == config.ALLOWED_LANGUAGES
