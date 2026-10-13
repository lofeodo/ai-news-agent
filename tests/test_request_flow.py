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


def _client(monkeypatch, subscribers):
    """TestClient on the router with a stubbed store; returns (client, sleeps, mails)."""
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    class Db:
        def collection(self, name):
            def document(i):
                def get():
                    d = subscribers.get(i)
                    return SimpleNamespace(exists=d is not None, to_dict=lambda: d)
                return SimpleNamespace(get=get, set=lambda *a, **k: None)
            return SimpleNamespace(document=document)

    sleeps, mails = [], []
    monkeypatch.setattr(subs, "_db", lambda: Db())
    monkeypatch.setattr(subs.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(subs.time, "monotonic", lambda: 100.0)  # no time passes in the handler
    monkeypatch.setattr(subs, "_send_email", lambda *a: mails.append(a))
    monkeypatch.setattr(subs.mail_throttle, "allow_mail", lambda db, email: True)
    monkeypatch.setattr(subs, "_active_subscriber_count", lambda db: 0)
    monkeypatch.setattr(subs.limiter, "enabled", False)
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    return TestClient(app), sleeps, mails


def test_link_routes_take_the_same_time_for_any_address(monkeypatch):
    client, sleeps, mails = _client(monkeypatch, {
        "on@example.com":  {"active": True, "token": "t"},
        "off@example.com": {"active": False, "token": "t"},
    })
    for route in ("/request-unsubscribe", "/request-preferences"):
        for email in ("on@example.com", "off@example.com", "none@example.com", "not-an-address"):
            sleeps.clear()
            assert client.post(route, json={"email": email}).status_code == 200
            assert sleeps == [subs.RESPONSE_FLOOR_SECONDS], (route, email)
    assert len(mails) == 2  # only the active address got mail


def test_subscribe_takes_at_least_the_floor(monkeypatch):
    client, sleeps, _ = _client(monkeypatch, {"on@example.com": {"active": True, "token": "t"}})
    for email in ("on@example.com", "new@example.com"):
        sleeps.clear()
        assert client.post("/subscribe", json={"email": email}).status_code == 200
        assert sleeps == [subs.RESPONSE_FLOOR_SECONDS]
