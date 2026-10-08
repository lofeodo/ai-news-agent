"""GET /preview: public sees the latest release run only; debug runs need the admin token. Firestore is stubbed."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs
import run_kind


def _snap(html, kind=None, variants=None):
    data = {"newsletter_variants": variants or {"0_1": html}}
    if kind:
        data["run_kind"] = kind
    return SimpleNamespace(exists=True, to_dict=lambda: data)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(subs, "ADMIN_TOKEN", "s3cret")
    monkeypatch.setattr(subs, "_db", lambda: SimpleNamespace(
        collection=lambda name: SimpleNamespace(document=lambda i: SimpleNamespace(get=lambda: _snap("<p>exact</p>", "debug")))))
    seen = []

    def fake_latest(db, collection, release_only=True):
        seen.append(release_only)
        return _snap("<p>debug</p>", "debug") if not release_only else _snap("<p>release</p>")

    monkeypatch.setattr(run_kind, "latest_run", fake_latest)
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    c = TestClient(app)
    c.seen = seen
    return c


def test_public_preview_is_release_only(client):
    r = client.get("/preview")
    assert r.status_code == 200 and "release" in r.text and client.seen == [True]


def test_debug_preview_needs_token(client):
    assert client.get("/preview?run=latest").status_code == 403
    assert client.get("/preview?run=latest&token=wrong").status_code == 403


def test_debug_preview_with_token(client):
    assert "debug" in client.get("/preview?run=latest&token=s3cret").text
    assert "exact" in client.get("/preview?run=2026-10-08T124639Z&token=s3cret").text
