"""Static checks on the newsletter pages: the wiring a browser test would only catch late."""
import os
import re

import agent_subscriptions as subs

SITE = os.path.join(os.path.dirname(__file__), "..", "public", "newsletter")
PAGES_WITH_TOPBAR = ["index", "issues", "login", "preferences", "sections", "share", "unsubscribe",
                     "newsletters", "newsletter"]


def _read(name):
    with open(os.path.join(SITE, name), encoding="utf-8") as f:
        return f.read()


def test_no_page_has_a_newsletters_tab():
    # Removed from the top bar: with only AI public it was a one-item list. The page itself stays.
    for page in PAGES_WITH_TOPBAR:
        html = _read(f"{page}.html")
        assert 'href="/newsletters.html" class="topbar__nav-link"' not in html, page


def test_return_page_allowlists_agree():
    js = re.search(r"ALLOWED_RETURN_PAGES = \[(.*?)\]", _read("auth.js")).group(1)
    assert set(re.findall(r"'([^']+)'", js)) == subs.ALLOWED_RETURN_PAGES


def test_the_email_form_names_newsletters_and_consent():
    js = _read("newsletters.js")
    assert "newsletters: ids" in js and "consent_text_version: consentVersion" in js
    for page in ("newsletters.html", "newsletter.html"):
        html = _read(page)
        assert "subscribeByEmail(" in html and "consent_text" in html, page


def test_issue_frames_stay_sandboxed():
    for page in ("newsletter.html", "issues.html"):
        for frame in re.findall(r"<iframe[^>]*>", _read(page)):
            assert 'sandbox="allow-popups allow-popups-to-escape-sandbox"' in frame, page
