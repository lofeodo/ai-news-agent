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
    assert html.index("SECTION:finance:Canada") < html.index("<!-- WIDGET -->")


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


# --- mixed-topic emails -------------------------------------------------------------------------------------

PAPER = {"title": "A paper", "authors": ["A. Author"], "summary": "Hook.", "hf_url": "https://huggingface.co/papers/1",
         "scores": {"upvotes": 12}}

MIXED = {"version": 1, "name": "My Monday brief", "accent_color": "#5b8def",
         "blocks": [
             {"type": "intro"},
             {"type": "widget", "widget": "paper_spotlight", "topic": "ai"},
             {"type": "section", "topic": "finance", "section": "Crypto & Fintech", "max_items": 3},
             {"type": "section", "topic": "gaming", "section": "Releases & Reviews", "max_items": 4},
             {"type": "section", "topic": "gaming", "section": "Industry & Business", "max_items": 2,
              "if_empty": "hide"},
             {"type": "section", "topic": "ai", "section": "Industry & Business", "max_items": 2},
             {"type": "custom_section", "title": "SpaceX", "query": "SpaceX launches", "max_items": 3},
             {"type": "share"},
         ]}


def _mixed_content(**kw):
    sneaky = {**_art(9, "fin"), "title": "Steal {{UNSUBSCRIBE_URL}} now", "summary": "{{PREFERENCES_URL}}"}
    base = dict(
        intro=None,
        sections={("finance", "Crypto & Fintech"): [_art(1, "fin", 300), _art(2, "fin"), sneaky],
                  ("gaming", "Releases & Reviews"): [_art(i, "game") for i in range(4)],
                  ("gaming", "Industry & Business"): [],
                  ("ai", "Industry & Business"): []},
        custom={"SpaceX": [_art(1, "spacex"), _art(2, "spacex")]},
        widgets={"paper_spotlight": {"papers": [PAPER]}},
    )
    base.update(kw)
    return render.Content(**base)


@pytest.fixture
def mixed():
    d = nd.parse(MIXED)
    return render.render(d, _mixed_content(), render.Look.for_definition(d, "October 19, 2026"))


def test_mixed_markers_are_prefixed_and_follow_the_blocks(mixed):
    assert _sections(mixed.html) == ["Research Spotlights", "finance:Crypto & Fintech", "gaming:Releases & Reviews",
                                     "ai:Industry & Business", "custom:SpaceX"]
    assert mixed.html.index("custom:SpaceX") < mixed.html.index("<!-- SHARE -->")


def test_placeholders_intact_and_none_from_article_text(mixed):
    html = mixed.html
    assert html.count("{{UNSUBSCRIBE_URL}}") == 1 and html.count("{{PREFERENCES_URL}}") == 1
    assert "Steal &#123;&#123;UNSUBSCRIBE_URL&#125;&#125; now" in html


def test_topic_labels_use_each_topics_accent(mixed):
    fin, gam = topics.get_topic("finance").accent_color, topics.get_topic("gaming").accent_color
    assert f'<span style="color:{fin};">Finance</span> &middot; Crypto & Fintech' in mixed.html
    assert f'<span style="color:{gam};">Video games</span> &middot; Releases & Reviews' in mixed.html
    assert '<span style="color:#5b8def;">Custom</span> &middot; SpaceX' in mixed.html


def test_toc_matches_the_sections_shown(mixed):
    toc = mixed.html.split("<!-- TOC -->")[1].split("<!-- /TOC -->")[0]
    labels = re.findall(r"font-weight:700;\">([^<]*)</span><span[^>]*>&thinsp;([^<]*)</span>", toc)
    assert [lab for lab in labels if lab[0]] == [("RES", "RESEARCH"), ("01", "CRYPTO"), ("02", "RELEASES"),
                                                 ("03", "INDUSTRY"), ("04", "SPACEX")]


def test_empty_sections_note_or_hide(mixed):
    assert "gaming:Industry & Business" not in mixed.html                    # if_empty: hide
    ai_industry = mixed.html.split("<!-- SECTION:ai:Industry & Business -->")[1].split("<!-- /SECTION")[0]
    assert "Nothing notable this week." in ai_industry                        # the default: a note


def test_templated_intro_and_masthead(mixed):
    assert ("This week: 3 stories in Crypto &amp; Fintech, 4 in Releases &amp; Reviews, and your custom section "
            "on SpaceX.") in mixed.html
    assert "<title>My Monday brief</title>" in mixed.html and "THIS WEEK" in mixed.html
    assert "logo-email.png" not in mixed.html and "EDITOR&apos;S NOTE" not in mixed.html
    assert 'color:#5b8def;">My Monday brief</h1>' in mixed.html


def test_disclaimers_once_per_used_topic(mixed):
    finance_note = topics.get_topic("finance").disclaimer
    footer = mixed.html.split("You're receiving this because you subscribed.")[1]
    assert footer.count(finance_note) == 1


def test_size_reported(mixed):
    assert mixed.size_bytes == len(mixed.html.encode("utf-8")) and mixed.warnings == []


def test_missing_custom_pool_drops_the_block_with_a_warning():
    d = nd.parse(MIXED)
    result = render.render(d, _mixed_content(custom={}), render.Look.for_definition(d, "wk"))
    assert "custom:SpaceX" not in result.html and "SpaceX" in result.warnings[0]
    assert "your custom section" not in result.html


def test_custom_titles_are_escaped():
    raw = {"version": 1, "name": "N", "accent_color": "#5b8def", "blocks": [
        {"type": "custom_section", "title": "R&D \"labs\"", "query": "labs", "max_items": 1}]}
    d = nd.parse(raw)
    html = render.render(d, render.Content(custom={"R&D \"labs\"": [_art(1)]}),
                         render.Look.for_definition(d, "wk")).html
    assert "<!-- SECTION:custom:R&amp;D &quot;labs&quot; -->" in html


def test_single_topic_definitions_keep_plain_markers_and_no_labels():
    raw = {"version": 1, "name": "N", "accent_color": "#5b8def", "blocks": [
        {"type": "section", "topic": "gaming", "section": "Releases & Reviews", "max_items": 2}]}
    d = nd.parse(raw)
    html = render.render(d, render.Content(), render.Look.for_definition(d, "wk")).html
    assert _sections(html) == ["Releases & Reviews"] and "&middot; Releases" not in html


def test_templated_intro_wording():
    from render.engine import templated_intro
    assert templated_intro([]) == "Your newsletter for this week."
    assert templated_intro([("section", "A", 1), ("section", "B", 0)]) == "This week: 1 story in A."
    assert templated_intro([("section", "A", 2), ("custom", "X", 1)]) == \
        "This week: 2 stories in A and your custom section on X."
