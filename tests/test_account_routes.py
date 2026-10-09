"""Account routes act only on addresses the sign-in provider verified. Tokens and Firestore are stubbed (no network)."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs
import auth_middleware


class FakeDb:
    def __init__(self):
        self.writes = []
        self.reads = 0
        self.collection = lambda name: SimpleNamespace(document=lambda i: self._ref(name, i))

    def _ref(self, name, i):
        def get():
            self.reads += 1
            return SimpleNamespace(exists=False, to_dict=lambda: {})
        rec = lambda kind: (lambda *a, **k: self.writes.append((name, i, kind)))
        return SimpleNamespace(get=get, set=rec("set"), update=rec("update"))


@pytest.fixture
def api(monkeypatch):
    claims = {"uid": "u1", "email": "a@example.com", "email_verified": True}
    seen = {}
    monkeypatch.setattr(auth_middleware, "_ensure_firebase", lambda: None)

    def fake_verify(token, check_revoked=False):
        seen["check_revoked"] = check_revoked
        if token == "bad":
            raise ValueError("bad token")
        return dict(claims)

    import firebase_admin.auth as fb_auth
    monkeypatch.setattr(fb_auth, "verify_id_token", fake_verify)
    monkeypatch.setattr(subs, "PREMIUM_EMAILS", {"a@example.com"})
    monkeypatch.setattr(subs.limiter, "enabled", False)
    db = FakeDb()
    monkeypatch.setattr(subs, "_db", lambda: db)
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    return SimpleNamespace(client=TestClient(app), claims=claims, db=db, seen=seen)


H = {"Authorization": "Bearer t"}
ROUTES = [
    ("post", "/auth/subscribe", {}),
    ("post", "/auth/unsubscribe", None),
    ("get", "/auth/preferences", None),
    ("post", "/auth/preferences", {"prefs": {"include_french": True, "include_canada": True}}),
    ("post", "/auth/sections/refine", {"raw_topic": "SpaceX"}),
    ("get", "/auth/sections", None),
    ("post", "/auth/sections", {"section_config": {"enabled_sections": None, "custom_sections": []}}),
    ("get", "/auth/admin/preview", None),
]


def _call(api, method, path, body):
    return getattr(api.client, method)(path, headers=H, **({"json": body} if body is not None else {}))


def test_missing_and_bad_token_are_401(api):
    assert api.client.get("/auth/me").status_code == 401
    assert api.client.get("/auth/me", headers={"Authorization": "Bearer bad"}).status_code == 401


def test_tokens_are_checked_for_revocation(api):
    api.client.get("/auth/me", headers=H)
    assert api.seen["check_revoked"] is True


@pytest.mark.parametrize("method,path,body", ROUTES)
@pytest.mark.parametrize("claim", [{"email_verified": False}, {"email_verified": None}, {"email": None}, {"email": ""}])
def test_account_routes_reject_unverified_or_missing_address(api, method, path, body, claim):
    api.claims.update(claim)
    r = _call(api, method, path, body)
    assert r.status_code == 403 and r.json()["detail"] == "email_not_verified"
    assert api.db.writes == [] and api.db.reads == 0


def test_me_for_unverified_address_is_free_and_writes_nothing(api):
    api.claims["email_verified"] = False
    body = api.client.get("/auth/me", headers=H).json()
    assert body["tier"] == "free" and body["is_admin"] is False and body["subscribed"] is False
    assert api.db.writes == [] and api.db.reads == 0


def test_premium_needs_a_verified_address(api):
    assert api.client.get("/auth/me", headers=H).json()["tier"] == "premium"
    assert api.client.get("/auth/sections", headers=H).status_code == 200
    api.claims["email_verified"] = False
    assert api.client.get("/auth/me", headers=H).json()["tier"] == "free"
    assert api.client.get("/auth/sections", headers=H).status_code == 403


def test_verified_address_reaches_routes(api):
    assert api.client.post("/auth/unsubscribe", headers=H).status_code == 200
    assert api.client.get("/auth/preferences", headers=H).json()["subscribed"] is False
