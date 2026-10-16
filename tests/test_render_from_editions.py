"""render_from_editions: custom newsletters assembled from editions (C2), custom pools (C5) and widget data."""
import copy
import json
import os
import re

import pytest

import newsletter_def as nd
import render

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "render", "editions.json")


@pytest.fixture
def data():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _render(data, **kw):
    args = dict(editions_by_topic=data["editions"], pools_by_query=data["pools"], widget_data=data["widget_data"])
    args.update(kw)
    return render.render_from_editions(nd.parse(data["definition"]), **args)


def _sections(html):
    return re.findall(r"<!-- SECTION:([^>]+?) -->", html)


def _links(html, marker):
    part = html.split(f"<!-- SECTION:{marker} -->")[1].split("<!-- /SECTION")[0]
    return list(dict.fromkeys(re.findall(r'href="(https://example\.com/[^"]+)"', part)))


def test_full_render(data):
    r = _render(data)
    assert r.warnings == []
    assert _sections(r.html) == ["Research Spotlights", "ai:Model & Product Releases", "finance:Crypto & Fintech",
                                 "gaming:Releases & Reviews", "custom:SpaceX"]
    assert "Small Models Can Check Their Own Work" in r.html
    assert "{{UNSUBSCRIBE_URL}}" in r.html and "{{PREFERENCES_URL}}" in r.html
    assert "October 19, 2026" in r.html
    assert r.size_bytes == len(r.html.encode("utf-8"))


def test_picks_then_runners_up_up_to_max_items(data):
    html = _render(data).html
    # finance: 2 picks + 1 runner-up = max_items 3
    assert _links(html, "finance:Crypto & Fintech") == ["https://example.com/fin/stablecoin-rules",
                                                        "https://example.com/fin/instant-transfers",
                                                        "https://example.com/fin/etf-flows"]
    assert len(_links(html, "gaming:Releases & Reviews")) == 4
    assert len(_links(html, "custom:SpaceX")) == 3


def test_english_only_is_the_default_and_uses_picks_en(data):
    html = _render(data).html
    links = _links(html, "ai:Model & Product Releases")
    assert "https://example.com/ai/modele-francais" not in links
    assert links == ["https://example.com/ai/laptop-model", "https://example.com/ai/review-mode",
                     "https://example.com/ai/speech"]


def test_french_readers_get_the_all_language_picks(data):
    links = _links(_render(data, prefs={"include_french": True}).html, "ai:Model & Product Releases")
    assert "https://example.com/ai/modele-francais" in links and "https://example.com/ai/speech" not in links


def test_english_only_filters_french_runners_up_too(data):
    data = copy.deepcopy(data)
    sec = data["editions"]["finance"]["sections"]["Crypto & Fintech"]
    sec["runners_up"].insert(0, {**sec["runners_up"][0], "url": "https://example.com/fin/fr", "language": "fr"})
    assert "https://example.com/fin/fr" not in _links(_render(data).html, "finance:Crypto & Fintech")


def test_hn_points_shown_only_for_hn(data):
    html = _render(data).html
    assert "&#9650;&nbsp;812" in html          # an HN pick
    assert "&#9650;&nbsp;1500" in html         # pool article with HN points


def test_missing_edition_drops_its_sections(data):
    editions = {k: v for k, v in data["editions"].items() if k != "gaming"}
    r = _render(data, editions_by_topic=editions)
    assert "gaming:Releases & Reviews" not in _sections(r.html)
    assert any("no edition of 'gaming'" in w for w in r.warnings)


def test_missing_section_in_an_edition(data):
    data = copy.deepcopy(data)
    del data["editions"]["finance"]["sections"]["Crypto & Fintech"]
    r = _render(data)
    assert "finance:Crypto & Fintech" not in _sections(r.html)
    assert any("not in this week's edition" in w for w in r.warnings)


def test_missing_pool_and_widget_data(data):
    r = _render(data, pools_by_query={}, widget_data={})
    assert _sections(r.html) == ["ai:Model & Product Releases", "finance:Crypto & Fintech",
                                 "gaming:Releases & Reviews"]
    assert any("SpaceX" in w for w in r.warnings) and any("paper_spotlight" in w for w in r.warnings)
    assert "RESEARCH" not in r.html


def test_empty_section_shows_a_note(data):
    raw = {"version": 1, "name": "Mine", "accent_color": "#5b8def", "blocks": [
        {"type": "section", "topic": "finance", "section": "Markets & Macro", "max_items": 2},
        {"type": "section", "topic": "finance", "section": "Crypto & Fintech", "max_items": 1}]}
    html = render.render_from_editions(nd.parse(raw), data["editions"]).html
    assert "Nothing notable this week." in html


def test_size_warning_on_an_oversized_definition(data):
    data = copy.deepcopy(data)
    long = "A long summary sentence that goes on. " * 400
    for sec in data["editions"]["gaming"]["sections"].values():
        for a in sec["picks"] + sec["runners_up"]:
            a["summary"] = long
    r = _render(data)
    assert r.size_bytes > render.SIZE_WARNING_BYTES
    assert any("Gmail cuts off" in w for w in r.warnings)
    assert not any("Gmail" in w for w in _render(json.loads(open(FIXTURE, encoding="utf-8").read())).warnings)


def test_week_label_from_the_editions_or_given(data):
    assert "October 26, 2026" in _render(data, week_of="October 26, 2026").html
