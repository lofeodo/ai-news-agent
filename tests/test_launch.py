"""The per-topic launch switch (agents/launch.py) and the public GET /newsletters list."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs
import auth_middleware
import launch
from fakes_firestore import FakeDb


# --- launch.py ------------------------------------------------------------------------------------

def test_ai_is_always_public(monkeypatch):
    monkeypatch.delenv("PUBLIC_TOPICS", raising=False)
    assert launch.public_topic_ids() == ["ai"]
    assert launch.is_public("ai") and not launch.is_public("finance")


def test_public_topics_from_the_env_in_site_order(monkeypatch):
    monkeypatch.setenv("PUBLIC_TOPICS", " Gaming, finance ,ai,gaming")
    assert launch.public_topic_ids() == ["ai", "finance", "gaming"]


def test_unknown_ids_are_ignored_and_logged_once(monkeypatch, capsys):
    monkeypatch.setattr(launch, "_warned", set())
    monkeypatch.setenv("PUBLIC_TOPICS", "finance,nope,../x")
    assert launch.public_topic_ids() == ["ai", "finance"]
    launch.public_topic_ids()
    out = capsys.readouterr().out
    assert out.count("'nope'") == 1 and "'../x'" in out


def test_admins_see_every_topic(monkeypatch):
    monkeypatch.delenv("PUBLIC_TOPICS", raising=False)
    every = launch.visible_topic_ids(is_admin=True)
    assert every[0] == "ai" and set(every) == {"ai", "finance", "gaming", "boardgames", "printing3d"}
    assert launch.is_visible("finance", is_admin=True) and not launch.is_visible("finance")


# --- GET /newsletters -----------------------------------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    monkeypatch.delenv("PUBLIC_TOPICS", raising=False)
    monkeypatch.setattr(subs, "ADMIN_EMAILS", {"boss@example.com"})
    monkeypatch.setattr(subs.limiter, "enabled", False)
    monkeypatch.setattr(auth_middleware, "_ensure_firebase", lambda: None)
    import firebase_admin.auth as fb_auth

    def fake_verify(token, check_revoked=False):
        if token == "admin":
            return {"uid": "a", "email": "boss@example.com", "email_verified": True}
        if token == "reader":
            return {"uid": "r", "email": "reader@example.com", "email_verified": True}
        raise ValueError("bad token")

    monkeypatch.setattr(fb_auth, "verify_id_token", fake_verify)
    db = FakeDb({"pipeline_runs": {
        "ai-1": {"started_at": "2026-10-11T16:00:00Z", "run_kind": "release", "newsletter_composed": True},
        "fin-1": {"started_at": "2026-10-11T14:00:00Z", "run_kind": "release", "newsletter_composed": True,
                  "topic": "finance"},
        "fin-dbg": {"started_at": "2026-10-12T14:00:00Z", "run_kind": "debug", "newsletter_composed": True,
                    "topic": "finance"},
    }})
    monkeypatch.setattr(subs, "_db", lambda: db)
    monkeypatch.setitem(subs._LATEST_ISSUES_CACHE, "value", None)
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    return TestClient(app)


def _ids(resp):
    assert resp.status_code == 200
    return [n["id"] for n in resp.json()["newsletters"]]


def test_anonymous_sees_only_public_newsletters(api):
    body = api.get("/newsletters").json()
    assert [n["id"] for n in body["newsletters"]] == ["ai"]
    ai = body["newsletters"][0]
    assert ai["name"] == "Latent SpaceMail" and ai["hidden"] is False and ai["latest_issue"] == "ai-1"
    assert ai["sections"][0] == {"name": "Model & Product Releases", "icon": ai["sections"][0]["icon"]}
    assert body["consent_text_version"] == subs.subscriptions_model.CONSENT_CURRENT


def test_hidden_topic_absent_for_a_signed_in_reader_and_a_bad_token(api):
    assert _ids(api.get("/newsletters", headers={"Authorization": "Bearer reader"})) == ["ai"]
    assert _ids(api.get("/newsletters", headers={"Authorization": "Bearer bad"})) == ["ai"]


def test_admin_sees_hidden_topics_flagged(api):
    body = api.get("/newsletters", headers={"Authorization": "Bearer admin"}).json()
    by_id = {n["id"]: n for n in body["newsletters"]}
    assert set(by_id) == {"ai", "finance", "gaming", "boardgames", "printing3d"}
    assert by_id["finance"]["hidden"] is True and by_id["ai"]["hidden"] is False
    assert by_id["finance"]["latest_issue"] == "fin-1"          # the debug run is never "the latest issue"
    assert by_id["gaming"]["latest_issue"] is None


def test_launched_topic_is_public(api, monkeypatch):
    monkeypatch.setenv("PUBLIC_TOPICS", "finance")
    body = api.get("/newsletters").json()
    assert [n["id"] for n in body["newsletters"]] == ["ai", "finance"]
    assert body["newsletters"][1]["hidden"] is False
