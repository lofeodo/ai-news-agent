"""GET /issues/latest (public, release only) and GET /auth/admin/issues (admin only, any run). Firestore is stubbed."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs
import run_kind
from auth_middleware import get_current_user


def _snap(html, kind=None, doc_id="r1", topic=None):
    data = {"newsletter_variants": {"0_1": html}}
    if kind:
        data["run_kind"] = kind
    if topic:
        data["topic"] = topic
    return SimpleNamespace(id=doc_id, exists=True, to_dict=lambda: data)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(subs, "ADMIN_EMAILS", {"admin@example.com"})
    monkeypatch.setattr(subs, "_db", lambda: SimpleNamespace(
        collection=lambda name: SimpleNamespace(document=lambda i: SimpleNamespace(get=lambda: _snap("<p>exact</p>", "debug", i)))))
    seen = []

    def fake_latest(db, collection, release_only=True, topic="ai"):
        seen.append(release_only)
        if topic != "ai":
            seen.append(topic)
            return _snap(f"<p>{topic} draft</p>", "debug", f"{topic}-1", topic=topic) if topic != "empty" else None
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
    r = client.get("/issues/latest")
    assert r.status_code == 200 and "release" in r.text and client.seen == [True]


def test_public_preview_ignores_run_param(client):
    assert "release" in client.get("/issues/latest?run=latest").text


def test_admin_preview_requires_login(client):
    assert client.get("/auth/admin/issues").status_code == 401


def test_admin_preview_rejects_non_admin_and_unverified(client):
    _as(client, "someone@example.com")
    assert client.get("/auth/admin/issues").status_code == 403
    _as(client, "admin@example.com", verified=False)
    assert client.get("/auth/admin/issues").status_code == 403


def test_admin_sees_latest_debug_run_and_exact_run(client):
    _as(client, "Admin@Example.com")
    body = client.get("/auth/admin/issues").json()
    assert body["run_id"] == "d1" and body["run_kind"] == "debug" and "debug" in body["html"]
    assert client.seen == [False]
    exact = client.get("/auth/admin/issues?run=2026-10-08T124639Z").json()
    assert exact["run_id"] == "2026-10-08T124639Z" and "exact" in exact["html"]


def test_preview_sends_embed_policy(client):
    csp = client.get("/issues/latest").headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "sandbox" in csp
    for origin in ("latentspacemail.web.app", "latentspacemail.firebaseapp.com", "newsletter.lofeodo.com"):
        assert origin in csp


def test_empty_preview_sends_embed_policy(client, monkeypatch):
    monkeypatch.setattr(run_kind, "latest_run", lambda *a, **k: None)
    r = client.get("/issues/latest")
    assert r.status_code == 404 and r.headers["Content-Security-Policy"] == subs.PREVIEW_CSP


def test_preview_page_frame_has_limited_sandbox():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "public/newsletter/issues.html").read_text(encoding="utf-8")
    assert 'sandbox="allow-popups allow-popups-to-escape-sandbox"' in html
    assert "allow-scripts" not in html and "allow-same-origin" not in html


# --- topics -----------------------------------------------------------------------------------------

def test_admin_preview_reports_the_ai_topic_by_default(client):
    _as(client, "admin@example.com")
    body = client.get("/auth/admin/issues").json()
    assert body["topic"] == "ai" and body["topic_name"] == "Latent SpaceMail"
    assert client.seen == [False]                       # the AI lookup is called exactly as before


def test_admin_can_open_another_topics_latest_run(client, demo_topic):
    _as(client, "admin@example.com")
    body = client.get("/auth/admin/issues?topic=demo").json()
    assert body["run_id"] == "demo-1" and body["topic"] == "demo" and body["topic_name"] == "Demo Weekly"
    assert "demo draft" in body["html"]
    assert client.seen == [False, "demo"]


def test_topic_ai_is_the_default_lookup(client):
    _as(client, "admin@example.com")
    assert client.get("/auth/admin/issues?topic=ai").json()["run_id"] == "d1"
    assert client.seen == [False]


def test_topic_with_no_runs_is_a_404(client, monkeypatch, demo_topic):
    monkeypatch.setattr(run_kind, "latest_run", lambda *a, **k: None)
    _as(client, "admin@example.com")
    assert client.get("/auth/admin/issues?topic=demo").status_code == 404


def test_unknown_topic_is_a_400(client):
    _as(client, "admin@example.com")
    assert client.get("/auth/admin/issues?topic=nope").status_code == 400
    assert client.get("/auth/admin/issues?topic=../ai").status_code == 400


def test_topic_param_is_admin_only(client, demo_topic):
    _as(client, "someone@example.com")
    assert client.get("/auth/admin/issues?topic=demo").status_code == 403


def test_public_pages_ignore_the_topic_param(client, demo_topic):
    assert "release" in client.get("/issues/latest?topic=demo").text
    assert client.seen == [True]


def test_topic_list_is_admin_only(client):
    assert client.get("/auth/admin/topics").status_code == 401
    _as(client, "someone@example.com")
    assert client.get("/auth/admin/topics").status_code == 403


def test_topic_list_has_the_ai_newsletter_first(client, demo_topic):
    _as(client, "admin@example.com")
    found = client.get("/auth/admin/topics").json()["topics"]
    assert [t["id"] for t in found] == ["ai", "demo"]
    assert found[0]["name"] == "Latent SpaceMail" and found[1]["tagline"] == "A demo"
    assert all(set(t) == {"id", "name", "tagline"} for t in found)


def test_topic_list_has_every_shipped_profile(client):
    _as(client, "admin@example.com")
    ids_ = [t["id"] for t in client.get("/auth/admin/topics").json()["topics"]]
    assert ids_[0] == "ai" and sorted(ids_) == ["ai", "boardgames", "finance", "gaming", "printing3d"]
