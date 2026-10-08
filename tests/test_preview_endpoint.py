"""GET /preview (public, release only) and GET /auth/admin/preview (admin only, any run). Firestore is stubbed."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs
import run_kind
from auth_middleware import get_current_user


def _snap(html, kind=None, doc_id="r1"):
    data = {"newsletter_variants": {"0_1": html}}
    if kind:
        data["run_kind"] = kind
    return SimpleNamespace(id=doc_id, exists=True, to_dict=lambda: data)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(subs, "ADMIN_EMAILS", {"admin@example.com"})
    monkeypatch.setattr(subs, "_db", lambda: SimpleNamespace(
        collection=lambda name: SimpleNamespace(document=lambda i: SimpleNamespace(get=lambda: _snap("<p>exact</p>", "debug", i)))))
    seen = []

    def fake_latest(db, collection, release_only=True):
        seen.append(release_only)
        return _snap("<p>debug</p>", "debug", "d1") if not release_only else _snap("<p>release</p>")

    monkeypatch.setattr(run_kind, "latest_run", fake_latest)
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    c = TestClient(app)
    c.app, c.seen = app, seen
    return c


def _as(client, email, verified=True):
    client.app.dependency_overrides[get_current_user] = lambda: {"email": email, "email_verified": verified, "uid": "u"}


def test_public_preview_is_release_only(client):
    r = client.get("/preview")
    assert r.status_code == 200 and "release" in r.text and client.seen == [True]


def test_public_preview_ignores_run_param(client):
    assert "release" in client.get("/preview?run=latest").text


def test_admin_preview_requires_login(client):
    assert client.get("/auth/admin/preview").status_code == 401


def test_admin_preview_rejects_non_admin_and_unverified(client):
    _as(client, "someone@example.com")
    assert client.get("/auth/admin/preview").status_code == 403
    _as(client, "admin@example.com", verified=False)
    assert client.get("/auth/admin/preview").status_code == 403


def test_admin_sees_latest_debug_run_and_exact_run(client):
    _as(client, "Admin@Example.com")
    body = client.get("/auth/admin/preview").json()
    assert body["run_id"] == "d1" and body["run_kind"] == "debug" and "debug" in body["html"]
    assert client.seen == [False]
    exact = client.get("/auth/admin/preview?run=2026-10-08T124639Z").json()
    assert exact["run_id"] == "2026-10-08T124639Z" and "exact" in exact["html"]
