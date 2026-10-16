"""GET /auth/admin/widgets: admin only; every widget's sample render, placements and latest real render."""
import pytest

import admin_widgets
from subs_api import AS_ADMIN, AS_READER, make_api
from widgets import registry
from widgets.store import save_edition
from widgets.runner import RunResult


@pytest.fixture
def api(monkeypatch):
    a = make_api(monkeypatch, runs={
        "r_new": {"started_at": "2026-10-18T16:00:00Z", "run_kind": "debug", "topic": "printing3d",
                  "widgets_summary": {"release_tracker": "failed"}},
    })
    a.client.app.include_router(admin_widgets.router)
    monkeypatch.setattr(admin_widgets, "_db", lambda: a.db)
    return a


def test_requires_an_admin(api):
    assert api.client.get("/auth/admin/widgets").status_code == 401
    assert api.client.get("/auth/admin/widgets", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert api.client.get("/auth/admin/widgets", headers=AS_READER).status_code == 403


def test_lists_every_widget_with_a_sample_page(api):
    body = api.client.get("/auth/admin/widgets", headers=AS_ADMIN).json()
    ids = [w["id"] for w in body["widgets"]]
    assert set(ids) == {w.id for w in registry.all_widgets()}
    bgg = next(w for w in body["widgets"] if w["id"] == "bgg_hot")
    assert bgg["commercial_use"] is False and bgg["keys"] == ["BGG_API_TOKEN"]
    paper = next(w for w in body["widgets"] if w["id"] == "paper_spotlight")
    assert paper["placed_in"] == [{"topic": "ai", "placement": "after_intro"}]
    for w in body["widgets"]:
        assert w["sample_html"].startswith("<!DOCTYPE html>") and "<script" not in w["sample_html"].lower(), w["id"]
        assert w["latest"] is None


def test_latest_render_comes_from_the_newest_ok_run(api):
    older = {"started_at": "2026-10-11T16:00:00Z", "run_kind": "release", "topic": "printing3d"}
    api.db.collection("pipeline_runs").document("r_old").set(older)
    data = registry.get("release_tracker").sample_data()
    save_edition(api.db, "r_old", RunResult(data={"release_tracker": data},
                                            status={"release_tracker": {"status": "ok"}}))
    body = api.client.get("/auth/admin/widgets", headers=AS_ADMIN).json()
    rt = next(w for w in body["widgets"] if w["id"] == "release_tracker")
    assert rt["last_status"] == {"status": "failed", "run_id": "r_new"}
    assert rt["latest"]["run_id"] == "r_old" and rt["latest"]["topic"] == "printing3d"
    assert "Marlin firmware" in rt["latest"]["html"]


def test_firestore_trouble_still_returns_samples(api, monkeypatch):
    def boom():
        raise RuntimeError("down")
    monkeypatch.setattr(admin_widgets, "_db", boom)
    body = api.client.get("/auth/admin/widgets", headers=AS_ADMIN).json()
    assert body["widgets"] and all(w["latest"] is None for w in body["widgets"])
