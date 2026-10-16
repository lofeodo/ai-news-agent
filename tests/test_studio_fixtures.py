"""The newsletter studio's mock fixtures (public/newsletter/studio/fixtures/) match the real code.

The studio runs against a mock of the studio API (contract C6) until roadmap E. These tests keep the mock honest:
the catalog has the exact C6 shape and names only real topics and sections, the email fragments are what the real
block renderer draws (regenerate with python tests/make_studio_fixtures.py), every section has sample articles,
and the example definitions pass the real C3 validator.
"""
import json
import os

import pytest

import make_studio_fixtures as fx

TOPIC_KEYS = {"id", "name", "tagline", "accent_color", "public", "sections"}
SECTION_KEYS = {"name", "definition", "icon"}
WIDGET_KEYS = {"id", "name", "description", "topics", "scope", "premium", "config_fields"}
FIELD_TYPES = {"tickers", "choice", "int", "bool"}


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_text(path) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n")   # a Windows checkout may convert line endings


@pytest.fixture(scope="module")
def catalog():
    return _load(fx.CATALOG_PATH)


def test_catalog_has_the_c6_shape(catalog):
    assert set(catalog) == {"topics", "widgets"}
    for t in catalog["topics"]:
        assert set(t) == TOPIC_KEYS, t["id"]
        assert isinstance(t["public"], bool)
        assert t["sections"], t["id"]
        for s in t["sections"]:
            assert set(s) == SECTION_KEYS
            assert all(isinstance(s[k], str) and s[k] for k in SECTION_KEYS)
    for w in catalog["widgets"]:
        assert set(w) == WIDGET_KEYS, w["id"]
        assert w["scope"] in ("edition", "personal")
        assert isinstance(w["premium"], bool)
        assert isinstance(w["topics"], list) and w["topics"]
        for field in w["config_fields"]:
            assert {"name", "type", "label", "default"} <= set(field), (w["id"], field)
            assert field["type"] in FIELD_TYPES
            if field["type"] == "choice":
                assert field["default"] in field["options"]


def test_catalog_is_what_the_generator_builds(catalog):
    assert catalog == json.loads(json.dumps(fx.build_catalog()))


def test_catalog_topics_match_the_profiles(catalog):
    import launch
    import topics

    assert [t["id"] for t in catalog["topics"]] == launch.ordered(topics.list_topic_ids())
    for t in catalog["topics"]:
        profile = topics.get_topic(t["id"])
        assert (t["name"], t["tagline"], t["accent_color"]) == (profile.name, profile.tagline, profile.accent_color)
        assert [(s["name"], s["definition"], s["icon"]) for s in t["sections"]] == \
            [(c.name, c.definition, c.icon) for c in profile.categories]


def test_widgets_are_well_formed_and_never_non_commercial(catalog):
    import topics

    ids = [w["id"] for w in catalog["widgets"]]
    assert len(ids) == len(set(ids))
    assert "paper_spotlight" in ids
    assert "bgg_hot" not in ids          # BoardGameGeek forbids commercial use: never listed in the studio
    known = set(topics.list_topic_ids())
    for w in catalog["widgets"]:
        assert w["topics"] == ["*"] or set(w["topics"]) <= known, w["id"]
        if w["scope"] == "personal":
            assert w["premium"], w["id"]


def test_mock_content_is_what_the_generator_builds():
    assert _load(fx.MOCK_CONTENT_PATH) == json.loads(json.dumps(fx.build_mock_content()))


def test_fragments_are_what_the_renderer_draws():
    expected = fx.build_fragments()
    on_disk = sorted(n for n in os.listdir(fx.FRAGMENT_DIR) if n.endswith(".html"))
    assert on_disk == sorted(expected), "run python tests/make_studio_fixtures.py"
    for name, html in expected.items():
        assert _read_text(os.path.join(fx.FRAGMENT_DIR, name)) == html, \
            f"{name} is stale: run python tests/make_studio_fixtures.py"


def test_every_widget_has_a_fragment(catalog):
    for w in catalog["widgets"]:
        assert os.path.isfile(os.path.join(fx.FRAGMENT_DIR, f"widget-{w['id']}.html")), w["id"]


def test_fragments_are_email_safe():
    for name, html in fx.build_fragments().items():
        low = html.lower()
        assert "<script" not in low and "javascript:" not in low, name
        assert "http://" not in low, name


def test_every_section_has_five_sample_articles(catalog):
    samples = _load(fx.SAMPLES_PATH)
    for t in catalog["topics"]:
        for s in t["sections"]:
            articles = samples.get(t["id"], {}).get(s["name"])
            assert articles and len(articles) >= 5, f"{t['id']}/{s['name']}"
            for a in articles:
                assert a["title"] and a["summary"]
                assert "<" not in a["title"] + a["summary"]
                assert a["points_source"] in (None, "hn")
                assert (a["points"] is None) == (a["points_source"] is None)


@pytest.mark.parametrize("name", sorted(os.listdir(fx.DEFINITIONS_DIR)))
def test_example_definitions_pass_the_c3_validator(catalog, name):
    import newsletter_def

    widgets = {w["id"]: {"scope": w["scope"], "topics": tuple(w["topics"]), "premium": w["premium"],
                         "commercial_use": True} for w in catalog["widgets"]}
    raw = _load(os.path.join(fx.DEFINITIONS_DIR, name))
    parsed = newsletter_def.parse(raw, widgets=widgets, custom=True, premium=True)
    assert newsletter_def.to_dict(parsed) == raw
