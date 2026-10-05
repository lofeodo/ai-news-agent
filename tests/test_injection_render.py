"""Deterministic injection tests for the newsletter renderers (no network, no keys).

Tests marked xfail(strict=True) pin weaknesses found in the Step 4 audit; the fix
commit for each removes its marker, and strict mode makes a silent fix fail loudly.
"""
import pytest

import agent3_compose as a3

HOSTILE = '<script>alert(1)</script><img src=x onerror=alert(1)>'
PLACEHOLDERS = ("{{UNSUBSCRIBE_URL}}", "{{PREFERENCES_URL}}")


def _article(**kw):
    base = {"title": "T", "url": "https://example.com/a", "summary": "S", "hn_score": 10}
    base.update(kw)
    return base


def _paper(**kw):
    base = {"title": "P", "authors": ["A"], "pdf_url": "https://arxiv.org/pdf/1", "summary": "One.\n\nTwo.",
            "scores": {"total": 20}}
    base.update(kw)
    return base


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<script>1</script>",
    "file:///etc/passwd", "ftp://example.com/x", "//evil.example/x", " https://example.com",
    "\thttps://example.com", "HTTPS://example.com", "vbscript:msgbox(1)", "", None, 42,
])
def test_safe_url_rejects_non_http(url):
    assert a3._safe_url(url) == "#"


@pytest.mark.parametrize("url", ["https://example.com/a?b=c", "http://example.com"])
def test_safe_url_accepts_http(url):
    assert a3._safe_url(url) == url


@pytest.mark.parametrize("url", ['https://x.example/a"onmouseover="alert(1)', "https://x.example/a'><script>"])
def test_safe_url_result_cannot_break_out_of_attribute(url):
    out = a3._safe_url(url)
    assert '"' not in out and "<" not in out and "'" not in out


def test_article_card_escapes_text_fields():
    html = a3.render_article_card(_article(title=HOSTILE, summary=HOSTILE))
    assert "<script" not in html and "<img src=x" not in html


def test_article_card_blocks_javascript_url():
    html = a3.render_article_card(_article(url="javascript:alert(1)"))
    assert "javascript:" not in html


def test_article_card_url_cannot_break_out_of_href():
    html = a3.render_article_card(_article(url='https://x.example/a"onmouseover="alert(1)'))
    assert 'onmouseover="' not in html


def test_paper_card_escapes_text_fields():
    html = a3.render_paper_card(_paper(title=HOSTILE, authors=[HOSTILE], summary=HOSTILE + "\n\n" + HOSTILE))
    assert "<script" not in html and "<img src=x" not in html


def test_paper_card_url_cannot_break_out_of_href():
    html = a3.render_paper_card(_paper(pdf_url='https://x.example/a"onmouseover="alert(1)'))
    assert 'onmouseover="' not in html


@pytest.mark.parametrize("include_canada", [True, False])
def test_compose_html_escapes_hostile_content_in_every_section(include_canada):
    selected = {cat: [_article(title=HOSTILE, summary=HOSTILE)] for cat in a3.NEWS_CATEGORIES}
    html = a3.compose_html(HOSTILE, [_paper(title=HOSTILE)], selected, "October 05, 2026", include_canada=include_canada)
    assert "<script" not in html and "<img src=x" not in html
    assert "javascript:" not in html


@pytest.mark.xfail(strict=True, reason="placeholder literals in article text are substituted by agent4")
def test_article_text_cannot_inject_footer_placeholders():
    clean = a3.compose_html("intro", [_paper()], {c: [_article()] for c in a3.NEWS_CATEGORIES}, "wk")
    hostile = a3.compose_html(
        "intro", [_paper()],
        {c: [_article(title="{{UNSUBSCRIBE_URL}}", summary="{{PREFERENCES_URL}}")] for c in a3.NEWS_CATEGORIES},
        "wk",
    )
    for ph in PLACEHOLDERS:
        assert hostile.count(ph) == clean.count(ph)
