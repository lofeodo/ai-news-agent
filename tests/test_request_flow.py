"""Request handling in the subscription service: blocking work runs off the event loop, mail calls time out."""
import inspect

import agent_subscriptions as subs
import auth_middleware

SYNC_HANDLERS = [
    "auth_me", "auth_unsubscribe", "auth_google_exchange", "auth_get_preferences",
    "auth_update_preferences", "auth_sections_refine", "auth_get_sections",
    "auth_update_sections", "admin_preview",
]


def test_blocking_handlers_are_plain_functions():
    for name in SYNC_HANDLERS:
        assert not inspect.iscoroutinefunction(getattr(subs, name)), name
    assert not inspect.iscoroutinefunction(auth_middleware.get_current_user)


def test_account_subscribe_hands_work_to_a_thread():
    assert inspect.iscoroutinefunction(subs.auth_subscribe)
    assert not inspect.iscoroutinefunction(subs._subscribe_account)


def test_mail_call_has_a_timeout(monkeypatch):
    import urllib.request
    seen = {}

    class Resp:
        status = 202
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return Resp()

    monkeypatch.setattr(subs, "_get_sendgrid_api_key", lambda: "k")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    subs._send_email("a@example.com", "s", "<p>x</p>")
    assert seen["timeout"] == subs.SENDGRID_TIMEOUT_SECONDS


def test_refine_client_has_a_timeout(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            seen.update(kw)
            raise RuntimeError("stop")

    monkeypatch.setattr(subs.anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(subs, "_get_anthropic_api_key", lambda: "k")
    monkeypatch.setattr(subs, "_get_user_tier", lambda u: "premium")
    from fastapi import HTTPException
    import pytest
    with pytest.raises(HTTPException):
        subs.auth_sections_refine.__wrapped__(
            None, subs.SectionRefineRequest(raw_topic="x"), {"uid": "u", "email": "a@example.com"}
        )
    assert seen["timeout"] == subs.REFINE_TIMEOUT_SECONDS
