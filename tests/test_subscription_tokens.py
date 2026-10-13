"""Subscription links and sign-in behavior. Firestore and tokens are stubbed (no network)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs

NAV_JS = Path(__file__).resolve().parent.parent / "public" / "newsletter" / "nav.js"

NOW = datetime.now(timezone.utc)
PAST = NOW - timedelta(days=30)
FUTURE = NOW + timedelta(days=30)


def test_nav_only_reads_account_state():
    src = NAV_JS.read_text(encoding="utf-8")
    assert "/auth/subscribe" not in src
    assert "/auth/me" in src


class _Ref:
    def __init__(self, store, email):
        self._store, self._email = store, email

    def get(self):
        data = self._store.get(self._email)
        return SimpleNamespace(exists=data is not None, id=self._email, reference=self,
                               to_dict=lambda: dict(data or {}))

    def update(self, fields):
        self._store[self._email].update(fields)

    def set(self, data, merge=False):
        self._store[self._email] = {**self._store.get(self._email, {}), **data} if merge else dict(data)


class _Query:
    def __init__(self, store, filters=()):
        self._store, self._filters = store, filters

    def where(self, field, op, value):
        return _Query(self._store, self._filters + ((field, value),))

    def limit(self, n):
        return self

    def stream(self):
        return [_Ref(self._store, e).get() for e, d in self._store.items()
                if all(d.get(f) == v for f, v in self._filters)]

    def count(self):
        return SimpleNamespace(get=lambda: [[SimpleNamespace(value=len(self.stream()))]])


class FakeDb:
    def __init__(self, store):
        self.store = store

    def collection(self, name):
        db = self
        assert name == subs.SUBSCRIBERS_COLLECTION
        return SimpleNamespace(
            document=lambda email: _Ref(db.store, email),
            where=lambda *a: _Query(db.store).where(*a),
        )


def _sub(token="tok", active=True, expires=PAST, **extra):
    return {"email": "a@example.com", "token": token, "active": active,
            "token_expires_at": expires, "prefs": {"include_french": True, "include_canada": True}, **extra}


@pytest.fixture
def api(monkeypatch):
    store = {}
    monkeypatch.setattr(subs, "_db", lambda: FakeDb(store))
    monkeypatch.setattr(subs.limiter, "enabled", False)
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    return SimpleNamespace(client=TestClient(app), store=store)


def test_active_subscriber_link_after_expiry_date(api):
    api.store["a@example.com"] = _sub(active=True, expires=PAST)
    assert api.client.get("/preferences", params={"token": "tok"}).status_code == 200
    r = api.client.post("/preferences", json={"token": "tok", "prefs": {"include_french": False, "include_canada": False}})
    assert r.status_code == 200
    assert api.store["a@example.com"]["prefs"]["include_french"] is False
    api.client.get("/unsubscribe", params={"token": "tok"})
    assert api.store["a@example.com"]["active"] is False


def test_pending_sign_up_link_after_expiry_date(api):
    api.store["a@example.com"] = _sub(active=False, expires=PAST)
    assert api.client.get("/preferences", params={"token": "tok"}).status_code == 404
    api.client.get("/unsubscribe", params={"token": "tok"})
    assert api.store["a@example.com"]["active"] is False  # unchanged, link refused


def test_confirm_link_checks_expiry_for_everyone(api):
    for active in (False, True):
        api.store["a@example.com"] = _sub(active=active, expires=PAST)
        api.client.get("/confirm", params={"token": "tok"})
        assert api.store["a@example.com"]["token"] == "tok"
        assert api.store["a@example.com"]["token_expires_at"] == PAST


def test_confirm_link_inside_window_still_works(api, monkeypatch):
    monkeypatch.setattr(subs, "_active_subscriber_count", lambda db: 0)
    api.store["a@example.com"] = _sub(active=False, expires=FUTURE)
    api.client.get("/confirm", params={"token": "tok"})
    stored = api.store["a@example.com"]
    assert stored["active"] is True and stored["token"] != "tok"


@pytest.fixture
def signed_in(api, monkeypatch):
    from auth_middleware import get_verified_user
    api.client.app.dependency_overrides[get_verified_user] = lambda: {"uid": "u1", "email": "a@example.com"}
    monkeypatch.setattr(subs, "_active_subscriber_count", lambda db: 0)
    return api


def test_reactivation_stores_a_working_link(signed_in):
    signed_in.store["a@example.com"] = _sub(token="old", active=False, expires=PAST)
    assert signed_in.client.post("/auth/subscribe", json={}).json() == {"status": "ok"}
    stored = signed_in.store["a@example.com"]
    assert stored["active"] is True and stored["uid"] == "u1"
    assert stored["token"] != "old"
    assert stored["token_expires_at"] > NOW + timedelta(days=300)
    assert signed_in.client.get("/preferences", params={"token": stored["token"]}).status_code == 200


def test_reactivation_email_carries_the_new_link(signed_in, monkeypatch):
    seen = {}
    monkeypatch.setattr(subs, "_latest_newsletter_html",
                        lambda db, unsubscribe_token, prefs=None: seen.setdefault("token", unsubscribe_token) and "<p>x</p>")
    monkeypatch.setattr(subs, "_send_email", lambda *a, **k: None)
    signed_in.store["a@example.com"] = _sub(token="old", active=False, expires=PAST)
    signed_in.client.post("/auth/subscribe", json={"send_latest": True})
    assert seen["token"] == signed_in.store["a@example.com"]["token"] != "old"


def test_already_active_subscriber_keeps_link(signed_in):
    signed_in.store["a@example.com"] = _sub(token="keep", active=True, expires=PAST)
    assert signed_in.client.post("/auth/subscribe", json={}).json() == {"status": "already_subscribed"}
    assert signed_in.store["a@example.com"]["token"] == "keep"
    assert signed_in.store["a@example.com"]["token_expires_at"] == PAST
