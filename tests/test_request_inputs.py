"""Addresses and ids that come from requests are checked before any lookup. Firestore is stubbed (no network)."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs
from fakes_firestore import FakeDb


class StrictDb(FakeDb):
    """Records every collection and document id the app asks for."""

    def __init__(self):
        super().__init__()
        self.touched = []

    def collection(self, name):
        coll = super().collection(name)
        real = coll.document
        coll.document = lambda i: (self.touched.append((name, i)), real(i))[1]
        return coll


@pytest.fixture
def api(monkeypatch):
    db = StrictDb()
    monkeypatch.setattr(subs, "_db", lambda: db)
    monkeypatch.setattr(subs.limiter, "enabled", False)
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    return SimpleNamespace(client=TestClient(app), db=db)


@pytest.mark.parametrize("path", ["/request-unsubscribe", "/request-preferences"])
def test_link_requests_for_odd_addresses_answer_ok_without_lookup(api, path):
    for email in ("a/b@example.com", "a@@example.com", "no-at-sign", "x y@example.com"):
        res = api.client.post(path, json={"email": email})
        assert res.status_code == 200 and res.json() == {"status": "ok"}
    assert api.db.touched == []


def test_subscribe_still_answers_422_for_a_malformed_address(api):
    res = api.client.post("/subscribe", json={"email": "a/b@example.com"})
    assert res.status_code == 422
    assert api.db.touched == []


def test_odd_run_ids_are_not_looked_up(api, monkeypatch):
    monkeypatch.setattr(subs, "_is_admin", lambda user: True)
    from auth_middleware import get_verified_user
    api.client.app.dependency_overrides[get_verified_user] = lambda: {"uid": "u", "email": "a@example.com"}
    res = api.client.get("/auth/admin/preview", params={"run": "x/y"})
    assert res.status_code == 404
    assert api.db.touched == []


def test_odd_sign_in_state_takes_the_invalid_state_path(api, monkeypatch):
    monkeypatch.setattr(subs, "FRONTEND_BASE_URL", "https://front.example")
    res = api.client.get("/auth/google/callback", params={"state": "a/b", "code": "c"}, follow_redirects=False)
    assert res.status_code == 302 and "auth_error=google_signin_failed" in res.headers["location"]
    assert api.db.touched == []
