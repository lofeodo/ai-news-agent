"""Section lists are capped and de-duplicated; saving works for an account with no user doc. Stubbed (no network)."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs
from auth_middleware import get_verified_user
from fakes_firestore import FakeDb


@pytest.fixture
def api(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(subs, "_db", lambda: db)
    monkeypatch.setattr(subs, "_get_user_tier", lambda user: "premium")
    monkeypatch.setattr(subs.limiter, "enabled", False)
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    app.dependency_overrides[get_verified_user] = lambda: {"uid": "u1", "email": "a@example.com"}
    return SimpleNamespace(client=TestClient(app), db=db)


def _post(api, cfg):
    return api.client.post("/auth/sections", json={"section_config": cfg})


def _custom(i, topic=None):
    return {"id": f"id{i}", "raw_input": "x", "refined_topic": topic or f"topic {i}"}


def test_save_creates_the_user_doc_when_missing(api):
    res = _post(api, {"enabled_sections": None, "custom_sections": []})
    assert res.status_code == 200
    assert api.db.store["users"]["u1"]["section_config"]["custom_sections"] == []


def test_save_keeps_other_user_fields(api):
    api.db.store["users"] = {"u1": {"tier": "premium"}}
    assert _post(api, {"enabled_sections": None, "custom_sections": []}).status_code == 200
    assert api.db.store["users"]["u1"]["tier"] == "premium"


def test_duplicate_enabled_sections_collapse_in_order(api):
    names = subs.DEFAULT_SECTIONS[:3]
    res = _post(api, {"enabled_sections": [names[1], names[0], names[1], names[2], names[0]], "custom_sections": []})
    assert res.status_code == 200
    assert api.db.store["users"]["u1"]["section_config"]["enabled_sections"] == [names[1], names[0], names[2]]


def test_duplicate_custom_sections_collapse(api):
    cfg = {"enabled_sections": None,
           "custom_sections": [_custom(1, "SpaceX"), _custom(2, " spacex "), _custom(1, "Other"), _custom(3)]}
    assert _post(api, cfg).status_code == 200
    saved = api.db.store["users"]["u1"]["section_config"]["custom_sections"]
    assert [c["id"] for c in saved] == ["id1", "id3"]


def test_over_the_list_limits_is_rejected(api):
    too_many_custom = [_custom(i) for i in range(subs.MAX_CUSTOM_SECTIONS + 1)]
    assert _post(api, {"enabled_sections": None, "custom_sections": too_many_custom}).status_code == 422
    too_many_enabled = [subs.DEFAULT_SECTIONS[0]] * (subs.MAX_ENABLED_SECTIONS + 1)
    assert _post(api, {"enabled_sections": too_many_enabled, "custom_sections": []}).status_code == 422
    assert "u1" not in api.db.store.get("users", {})
