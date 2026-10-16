"""The block renderer (agents/render/). Ready-made newsletters are pinned by test_render_goldens.py; these tests
cover what the definitions add: block order, the widget plug-in, mixed-topic emails and the size report."""
import re

import pytest

import newsletter_def as nd
import render
import topics


def _art(i, tag="x", hn=None):
    return {"title": f"{tag} story {i}", "url": f"https://news.example/{tag}/{i}", "summary": f"About {tag} {i}.",
            "hn_score": hn}


def _look(**kw):
    base = dict(name="My Monday brief", tagline="Custom weekly briefing", accent="#7fd1a0", link_color="#3f6850",
                week_of="October 19, 2026")
    base.update(kw)
    return render.Look(**base)


def _sections(html):
    return re.findall(r"<!-- SECTION:([^>]+?) -->", html)


@pytest.fixture(autouse=True)
def _address(monkeypatch):
    monkeypatch.setenv("MAILING_ADDRESS", "1 Example St")


def test_size_is_the_utf8_byte_length():
    d = nd.from_topic(topics.get_topic("finance"))
    result = render.render(d, render.Content(intro="Hi é"), _look())
    assert result.size_bytes == len(result.html.encode("utf-8")) > len(result.html)


def test_sections_follow_the_definition_order_and_max_items():
    raw = {"version": 1, "name": "Mine", "accent_color": "#7fd1a0", "blocks": [
        {"type": "section", "topic": "finance", "section": "Canada", "max_items": 1},
        {"type": "section", "topic": "finance", "section": "Markets & Macro", "max_items": 2},
    ]}
    content = render.Content(sections={("finance", "Canada"): [_art(1, "ca"), _art(2, "ca")],
                                       ("finance", "Markets & Macro"): [_art(i, "mm") for i in range(4)]})
    html = render.render(nd.parse(raw), content, _look()).html
    assert _sections(html) == ["Canada", "Markets & Macro"]
    assert "ca/1" in html and "ca/2" not in html
    assert "mm/1" in html and "mm/2" not in html


def test_widget_plug_in_is_called_in_place():
    raw = {"version": 1, "name": "Mine", "accent_color": "#7fd1a0", "blocks": [
        {"type": "section", "topic": "finance", "section": "Canada", "max_items": 1},
        {"type": "widget", "widget": "paper_spotlight", "topic": "ai"},
    ]}
    seen = []

    def plug(block, ctx):
        seen.append((block.widget, ctx.look.name))
        return "<!-- WIDGET -->\n"

    html = render.render(nd.parse(raw), render.Content(), _look(), render_widget=plug).html
    assert seen == [("paper_spotlight", "My Monday brief")]
    assert html.index("SECTION:Canada") < html.index("<!-- WIDGET -->")


def test_default_widget_renderer_drops_unknown_widgets_with_a_warning():
    raw = {"version": 1, "name": "Mine", "accent_color": "#7fd1a0",
           "blocks": [{"type": "widget", "widget": "market_snapshot", "topic": "finance"}]}
    d = nd.parse(raw, widgets={"market_snapshot"})
    result = render.render(d, render.Content(), _look())
    assert "market_snapshot" in result.warnings[0]


def test_variant_flags_apply_only_with_prefs():
    d = nd.from_topic(topics.get_topic("ai"))
    with_prefs = render.render(d, render.Content(), _look(), {"include_canada": False}).html
    without = render.render(d, render.Content(), _look()).html
    assert "Canada & Montreal" not in _sections(with_prefs)
    assert "Canada & Montreal" in _sections(without)
