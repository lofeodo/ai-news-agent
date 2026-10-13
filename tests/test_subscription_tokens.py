"""Subscription links and sign-in behavior. Firestore and tokens are stubbed (no network)."""
from pathlib import Path

NAV_JS = Path(__file__).resolve().parent.parent / "public" / "newsletter" / "nav.js"


def test_nav_only_reads_account_state():
    src = NAV_JS.read_text(encoding="utf-8")
    assert "/auth/subscribe" not in src
    assert "/auth/me" in src
