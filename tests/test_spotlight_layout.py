"""The spotlight paper leads the issue: placement, TOC order, premium reorder, card content."""
import agent3_compose as a3
import agent4_send as a4

CATS = {"Model & Product Releases": [{"title": "N1", "url": "https://e.com/1", "summary": "s"}],
        "Industry & Business": [{"title": "N2", "url": "https://e.com/2", "summary": "s"}]}
PAPER = {"title": "Loops", "authors": ["A", "B", "C", "D"], "hf_url": "https://huggingface.co/papers/2609.00001",
         "pdf_url": "https://arxiv.org/pdf/2609.00001", "summary": "One. Two. Three.",
         "scores": {"total": 30, "upvotes": 1671}}


def _html(papers=None):
    return a3.compose_html("Intro text", [PAPER] if papers is None else papers, CATS, "October 12, 2026")


def test_spotlight_sits_between_editors_note_and_first_news_section():
    html = _html()
    note = html.index("EDITOR")
    spot = html.index("<!-- SECTION:Research Spotlights -->")
    first = html.index("<!-- SECTION:Model")
    assert note < spot < first


def test_toc_lists_research_first_and_news_numbers_unchanged():
    toc = _html().split("<!-- TOC -->")[1].split("<!-- /TOC -->")[0]
    assert toc.index("RES") < toc.index("01")
    assert ">01<" in toc and ">02<" in toc


def test_card_links_to_hugging_face_and_shows_upvotes():
    card = a3.render_paper_card(PAPER)
    assert card.count("https://huggingface.co/papers/2609.00001") == 2
    assert "arxiv.org" not in card and "1,671" in card and "Read on Hugging Face" in card
    assert "A, B, C et al." in card


def test_card_falls_back_to_pdf_link_without_hf_url_or_upvotes():
    card = a3.render_paper_card({**PAPER, "hf_url": None, "scores": {"total": 3}})
    assert "https://arxiv.org/pdf/2609.00001" in card and "Read the paper" in card and "upvotes" not in card


def test_premium_reorder_keeps_spotlight_first_and_drops_it_when_disabled():
    html = _html()
    out = a4._apply_section_config(html, {"enabled_sections": ["Industry & Business", "Model & Product Releases", "Research Spotlights"]})
    assert out.index("SECTION:Research Spotlights") < out.index("SECTION:Industry & Business") < out.index("SECTION:Model & Product Releases")
    off = a4._apply_section_config(html, {"enabled_sections": ["Industry & Business"]})
    assert "SECTION:Research Spotlights" not in off
