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
