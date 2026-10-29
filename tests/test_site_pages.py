"""Static checks on the subscribe flow pages (no browser): the welcome page and the homepage's Google button."""
import re
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "public" / "newsletter"


def _read(name: str) -> str:
    return (SITE / name).read_text(encoding="utf-8")


def test_welcome_page_has_every_state_and_the_shared_scripts():
    src = _read("welcome.html")
    for state in ("st-working", "st-subscribed", "st-failed", "st-signed-out"):
        assert f'id="{state}"' in src, state
    assert 'src="nav.js"' in src and "from './auth.js'" in src
    assert "/auth/subscribe" in src
    assert "return_to=welcome.html" in src
    # Consent: the wording version comes from the catalog route, like the email forms.
    assert "/newsletters" in src and "consent_text_version" in src


def test_welcome_page_never_depends_on_storage_for_the_subscription():
    """The options are best effort; a missing key must still subscribe (see docs/plans/google-subscribe-flow.md)."""
    src = _read("welcome.html")
    m = re.search(r"function takeOptions\(\) \{(.*?)\n    \}", src, re.DOTALL)
    assert m and "catch { return null; }" in m.group(1)


def test_homepage_google_button_subscribes_through_the_welcome_page():
    src = _read("index.html")
    assert "return_to=welcome.html" in src
    assert "return_to=index.html" not in src
    assert "Subscribe with Google" in src and "Continue with Google" not in src
    assert 'class="signup__consent"' in src
    # The same storage key on both ends.
    assert "lsm_subscribe_opts" in src and "lsm_subscribe_opts" in _read("welcome.html")


def test_every_page_with_an_account_area_shows_subscription_state():
    pages = [p for p in SITE.glob("*.html") if 'id="topbar-auth"' in p.read_text(encoding="utf-8")]
    assert len(pages) >= 10
    for p in pages:
        assert 'id="topbar-sub"' in p.read_text(encoding="utf-8"), p.name
    nav = _read("nav.js")
    assert "topbar-sub" in nav and "/welcome.html" in nav and "lsm:me" in nav


def test_homepage_signed_in_block_reacts_to_subscription_state():
    src = _read("index.html")
    assert 'id="member-status"' in src and 'id="member-primary"' in src
    assert "lsm:me" in src and "/welcome.html" in src


def test_preferences_page_puts_subscribe_first_when_not_subscribed():
    src = _read("preferences.html")
    assert 'id="not-subscribed-card"' in src and 'id="card-subscribe-btn"' in src
    assert src.index('id="not-subscribed-card"') < src.index('id="newsletters-section"')
    assert "lsm:subscription" in src


def test_preferences_page_shows_one_newsletter_as_a_subscription_not_a_list():
    src = _read("preferences.html")
    assert "single = data.available.length === 1" in src
    assert "'Your subscription'" in src and "pref-nl--single" in src


def test_preferences_first_subscription_falls_back_to_the_subscribe_route():
    """POST /auth/preferences is 404 for an address with no subscriber record (seen live 2026-10-29)."""
    src = _read("preferences.html")
    i = src.index("res.status === 404 && box.checked")
    assert "/auth/subscribe" in src[i:i + 400]
