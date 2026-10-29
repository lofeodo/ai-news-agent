"""The sign-in return-page allowlist is kept twice (backend and auth.js); they must agree.

The return page is the only signal that survives the Google round trip, so the welcome page
(which performs the subscription after "Subscribe with Google") must be allowed on both sides.
"""
import re
from pathlib import Path

import agent_subscriptions as subs

SITE = Path(__file__).resolve().parent.parent / "public" / "newsletter"


def _frontend_allowlist() -> set[str]:
    src = (SITE / "auth.js").read_text(encoding="utf-8")
    m = re.search(r"const ALLOWED_RETURN_PAGES = \[(.*?)\];", src, re.DOTALL)
    assert m, "auth.js allowlist not found"
    return set(re.findall(r"'([^']+)'", m.group(1)))


def test_allowlists_match():
    assert _frontend_allowlist() == set(subs.ALLOWED_RETURN_PAGES)

