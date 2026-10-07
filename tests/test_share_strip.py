"""The one-line share strip: placed after the 2nd news section, survives premium section reordering."""
import agent3_compose as a3
import agent4_send as a4

CATS = {c: [{"title": f"T {c}", "url": "https://e.com", "summary": "s"}] for c in a3.NEWS_CATEGORIES}


def _html():
    return a3.compose_html("Intro text", [], CATS, "October 12, 2026")


def _order(html):
    import re
    return re.findall(r"<!-- (?:SECTION:([^>]+?)|(SHARE)) -->", html)


def test_strip_sits_between_second_and_third_news_section_once():
    html = _html()
    assert html.count("<!-- SHARE -->") == 1 and "share.html" in html
    names = [a or b for a, b in _order(html)]
    assert names.index("SHARE") == 3  # Research, news 1, news 2, SHARE


def _apply(html, enabled):
    return a4._apply_section_config(html, {"enabled_sections": enabled})


def test_strip_survives_reordering_and_follows_second_news_section():
    html = _apply(_html(), ["Safety & Alignment", "Industry & Business", "Open Source & Tools"])
    assert html.count("<!-- SHARE -->") == 1
    names = [a or b for a, b in _order(html)]
    assert names.index("SHARE") == names.index("Industry & Business") + 1


def test_strip_kept_at_end_when_only_one_news_section_enabled():
    html = _apply(_html(), ["Safety & Alignment"])
    assert html.count("<!-- SHARE -->") == 1


def test_old_newsletter_without_marker_is_unchanged():
    html = _html().replace("<!-- SHARE -->", "").replace("<!-- /SHARE -->", "")
    assert "<!-- SHARE -->" not in _apply(html, ["Safety & Alignment"])
