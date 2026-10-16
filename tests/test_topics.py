import copy

import pytest
import yaml

import topics
from topics import TopicError, get_topic, parse_topic


def _ai_raw() -> dict:
    with open("topics/ai.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _minimal() -> dict:
    """A small valid profile for a non-AI topic, reusing the AI prompt files (generic ones arrive later)."""
    return {
        "id": "demo", "name": "Demo Weekly", "tagline": "A demo", "audience": "demo readers",
        "accent_color": "#112233", "languages": ["en"], "variants": ["0_0"], "spotlight": "none",
        "categories": [{"name": "One", "definition": "first"}, {"name": "Two", "definition": "second", "cap": 5}],
        "sources": [{"type": "rss", "feeds": [{"name": "Feed", "url": "https://example.com/feed.xml"}]}],
        "prompts": _ai_raw()["prompts"],
    }


def test_ai_profile_loads():
    t = get_topic("ai")
    assert t.id == "ai" and t.is_default
    assert t.spotlight == "papers"
    assert len(t.category_names) == 7
    assert t.variants == ("0_0", "1_0", "0_1", "1_1")


def test_none_and_empty_mean_default():
    assert get_topic(None).id == "ai"
    assert get_topic("").id == "ai"
    assert get_topic(" AI ").id == "ai"


def test_unknown_topic_rejected():
    with pytest.raises(TopicError):
        get_topic("nope")
    with pytest.raises(TopicError):
        get_topic("../ai")


def test_minimal_profile_is_valid_with_defaults():
    t = parse_topic(_minimal())
    assert t.section_cap("One") == topics.DEFAULT_SECTION_CAP
    assert t.section_cap("Two") == 5
    assert t.max_articles == topics.DEFAULT_MAX_ARTICLES
    assert t.category_definitions() == "- One: first\n- Two: second"
    assert t.variant_prefs() == {"0_0": {"include_french": False, "include_canada": False}}


@pytest.mark.parametrize("mutate, fragment", [
    (lambda r: r.pop("name"), "'name'"),
    (lambda r: r.update(id="Bad Id"), "id must be"),
    (lambda r: r.update(accent_color="red"), "accent_color"),
    (lambda r: r.update(spotlight="video"), "spotlight"),
    (lambda r: r.update(languages=["en", "de"]), "languages"),
    (lambda r: r.update(languages=[]), "languages"),
    (lambda r: r.update(variants=["1_0"]), "0_0"),
    (lambda r: r.update(variants=["0_0", "9_9"]), "variants"),
    (lambda r: r.update(variants=["0_0", "1_0"]), "includes French"),
    (lambda r: r.update(categories=[]), "categories"),
    (lambda r: r.update(categories=[{"name": f"C{i}", "definition": "d"} for i in range(8)]), "categories"),
    (lambda r: r.update(categories=[{"name": "A", "definition": "d"}, {"name": "A", "definition": "e"}]), "unique"),
    (lambda r: r.update(categories=[{"name": "A", "definition": ""}]), "definition"),
    (lambda r: r.update(categories=[{"name": "A", "definition": "d", "cap": 0}]), "cap"),
    (lambda r: r.update(categories=[{"name": "A", "definition": "d", "variant_flag": "include_canada"}]), "never differ"),
    (lambda r: r.update(categories=[{"name": "A", "definition": "d", "variant_flag": "nonsense"}]), "variant_flag"),
    (lambda r: r.update(sources=[]), "sources"),
    (lambda r: r.update(sources=[{"type": "carrier_pigeon"}]), "source type"),
    (lambda r: r.update(sources=[{"type": "newsapi", "queries": []}]), "query strings"),
    (lambda r: r.update(sources=[{"type": "rss", "feeds": [{"name": "x", "url": "ftp://x"}]}]), "url"),
    (lambda r: r.update(sources=[{"type": "rss", "feeds": [{"name": "x", "url": "https://x", "max_items": 0}]}]), "max_items"),
    (lambda r: r.update(max_articles=-1), "max_articles"),
    (lambda r: r.update(prompts={"intro": "prompts/does_not_exist.txt"}), "does not exist"),
    (lambda r: r.update(prompts={"bogus": "prompts/intro_prompt.txt"}), "prompts may only"),
])
def test_bad_profiles_are_rejected(mutate, fragment):
    raw = _minimal()
    mutate(raw)
    with pytest.raises(TopicError) as e:
        parse_topic(raw)
    assert fragment in str(e.value)


def test_file_name_must_match_id():
    raw = _minimal()
    with pytest.raises(TopicError):
        parse_topic(raw, expected_id="other")


def test_french_variant_allowed_when_language_listed():
    raw = _minimal()
    raw["languages"] = ["en", "fr"]
    raw["variants"] = ["0_0", "1_0"]
    assert parse_topic(raw).variants == ("0_0", "1_0")


def test_variant_flag_category_follows_preferences():
    t = get_topic("ai")
    canada = t.category("Canada & Montreal")
    assert canada.variant_flag == "include_canada"
    assert t.category_active(canada, {"include_canada": True})
    assert not t.category_active(canada, {"include_canada": False})
    assert t.category_active("Industry & Business", {})


def test_topic_id_of_run_doc():
    assert topics.topic_id_of(None) == "ai"
    assert topics.topic_id_of({}) == "ai"
    assert topics.topic_id_of({"topic": "gaming"}) == "gaming"
    assert topics.is_default_topic({"run_kind": "debug"})
    assert not topics.is_default_topic({"topic": "gaming"})


def test_topic_for_run_local_reads_env(monkeypatch):
    monkeypatch.delenv("TOPIC", raising=False)
    assert topics.topic_for_run("r1", use_firestore=False).id == "ai"
    monkeypatch.setenv("TOPIC", "ai")
    assert topics.topic_for_run("r1", use_firestore=False).id == "ai"
    monkeypatch.setenv("TOPIC", "nope")
    with pytest.raises(TopicError):
        topics.topic_for_run("r1", use_firestore=False)


def test_every_profile_file_loads():
    for topic_id in topics.list_topic_ids():
        assert get_topic(topic_id).id == topic_id


def test_parse_does_not_mutate_input():
    raw = _minimal()
    before = copy.deepcopy(raw)
    parse_topic(raw)
    assert raw == before
