"""Public subscriber count and the owner-only stats route. Firestore is stubbed (no network)."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs


@pytest.fixture
def api(monkeypatch):
    calls = []
    monkeypatch.setattr(subs, "_db", lambda: object())
    monkeypatch.setattr(subs, "_active_subscriber_count", lambda _db: calls.append(1) or 7)
    monkeypatch.setattr(subs.limiter, "enabled", False)
    monkeypatch.setitem(subs._SPOTS_CACHE, "value", None)
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    return SimpleNamespace(client=TestClient(app), calls=calls)


def test_spots_returns_counts_and_caches(api):
    first = api.client.get("/spots")
    assert first.status_code == 200 and first.json() == {"active": 7, "max": subs.MAX_SUBSCRIBERS}
    assert api.client.get("/spots").json() == first.json()
    assert len(api.calls) == 1


def test_spots_requeries_after_the_cache_window(api, monkeypatch):
    api.client.get("/spots")
    monkeypatch.setitem(subs._SPOTS_CACHE, "at", subs._SPOTS_CACHE["at"] - subs.SPOTS_CACHE_SECONDS - 1)
    api.client.get("/spots")
    assert len(api.calls) == 2


def test_stats_is_off_without_a_configured_token(api, monkeypatch):
    monkeypatch.setattr(subs, "ADMIN_TOKEN", "")
    assert api.client.get("/stats").status_code == 404
    assert api.client.get("/stats", params={"token": ""}).status_code == 404
    assert api.calls == []


def test_stats_needs_the_token(api, monkeypatch):
    monkeypatch.setattr(subs, "ADMIN_TOKEN", "s3cret")
    assert api.client.get("/stats").status_code == 403
    assert api.client.get("/stats", params={"token": "nope"}).status_code == 403
    assert api.client.get("/stats", headers={"Authorization": "Bearer nope"}).status_code == 403
    ok_query  = api.client.get("/stats", params={"token": "s3cret"})
    ok_header = api.client.get("/stats", headers={"Authorization": "Bearer s3cret"})
    assert ok_query.status_code == ok_header.status_code == 200
    assert ok_header.json() == {"active": 7, "max": subs.MAX_SUBSCRIBERS}


def test_home_page_reads_the_public_count():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "public" / "newsletter" / "index.html").read_text(encoding="utf-8")
    assert "/spots" in html and "/stats" not in html
