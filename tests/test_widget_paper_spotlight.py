"""The paper of the week as a widget renders exactly today's card, alone and inside the full AI email."""
import json
import os

import pytest

import make_render_goldens as goldens
import newsletter_def
import render
import topics
from render.blocks import render_paper_card, research_rows
from widgets import ProduceContext, registry
from widgets.plugin import render_widget
from widgets.runner import produce_edition_widgets


def _papers():
    with open(goldens.INPUT_PATH, encoding="utf-8") as f:
        return json.load(f)["papers"]


def _verbose(p):
    """A paper as agent2a stores it: the card fields plus scoring detail the card never draws."""
    return {**p, "abstract": "x" * 3000, "arxiv_id": "2610.00001",
            "scores": {**(p.get("scores") or {}), "reasoning": "y" * 5000, "total": 30}}


def test_render_is_todays_card():
    w = registry.get("paper_spotlight")
    for p in _papers() + [w.sample_data()["papers"][0], {"title": "PDF only", "pdf_url": "https://arxiv.org/pdf/1"}]:
        html = w.render({"papers": [p]}, {}, None)
        assert html == research_rows([p])
        assert render_paper_card(p) in html


def test_produce_slims_and_still_renders_identically():
    w = registry.get("paper_spotlight")
    papers = _papers()
    res = produce_edition_widgets(ProduceContext(run_doc={"paper_summaries": [_verbose(p) for p in papers]}),
                                  [{"id": "paper_spotlight"}])
    assert res.summary() == {"paper_spotlight": "ok"}
    data = res.data["paper_spotlight"]
    assert "reasoning" not in json.dumps(data) and len(json.dumps(data)) < 2000
    assert w.render(data, {}, None) == research_rows(papers)


def test_produce_skips_when_no_paper_has_a_summary():
    res = produce_edition_widgets(ProduceContext(run_doc={"paper_summaries": [{"title": "t", "summary": " "}]}),
                                  [{"id": "paper_spotlight"}])
    assert res.summary() == {"paper_spotlight": "empty"}


@pytest.mark.parametrize("key", ["0_0", "1_0", "0_1", "1_1"])
def test_full_ai_email_through_the_registry_plugin_matches_the_golden(key, monkeypatch):
    monkeypatch.setenv("MAILING_ADDRESS", goldens.MAILING_ADDRESS)
    with open(goldens.INPUT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    topic = topics.get_topic("ai")
    prefs = topic.variant_prefs()[key]
    sel = data["topics"]["ai"]["selected_all" if prefs["include_french"] else "selected_en"]
    content = render.Content(intro=data["intro"], sections={("ai", c): a for c, a in sel.items()},
                             widgets={"paper_spotlight": {"papers": data["papers"]}})
    html = render.render(newsletter_def.from_topic(topic), content, render.Look.from_topic(topic, data["week_of"]),
                         {"include_canada": prefs["include_canada"]}, render_widget=render_widget).html
    with open(os.path.join(goldens.FIXTURE_DIR, f"ai_{key}.html"), encoding="utf-8", newline="") as f:
        assert html == f.read()


def test_plugin_leaves_out_unknown_missing_and_broken_widgets(monkeypatch):
    def ctx(widgets):
        return render.RenderContext(None, render.Content(widgets=widgets), None, None, [])
    block = newsletter_def.WidgetBlock("paper_spotlight", "ai")
    c = ctx({})
    assert render_widget(block, c) == "" and "no data" in c.warnings[0]
    c = ctx({})
    assert render_widget(newsletter_def.WidgetBlock("nope", "ai"), c) == "" and "unknown" in c.warnings[0]
    monkeypatch.setattr(type(registry.get("paper_spotlight")), "render", lambda self, d, cfg, look: 1 / 0)
    c = ctx({"paper_spotlight": {"papers": []}})
    assert render_widget(block, c) == "" and "render failed" in c.warnings[0]
