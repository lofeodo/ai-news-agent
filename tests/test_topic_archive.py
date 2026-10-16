"""The archive per newsletter: GET /issues?topic= and /issues/{run_id} for visible newsletters only."""
import run_kind
from subs_api import AS_ADMIN, AS_READER, issue, make_api

RUNS = {
    "ai-1":   issue("2026-10-11T16:00:00Z", html="<p>ai one</p>"),
    "ai-old": {"started_at": "2026-06-01T10:00:00Z", "newsletter_composed": True},      # unflagged: not archived
    "fin-1":  issue("2026-10-11T14:00:00Z", topic="finance", html="<p>finance one</p>"),
    "fin-2":  issue("2026-10-18T14:00:00Z", topic="finance", sent=False, html="<p>finance two</p>"),
    "fin-dbg": issue("2026-10-19T14:00:00Z", topic="finance", kind="debug"),
}


def _ids(resp):
    assert resp.status_code == 200, resp.text
    return [i["run_id"] for i in resp.json()["issues"]]


def test_ai_archive_is_unchanged(monkeypatch):
    api = make_api(monkeypatch, runs=RUNS, public_topics="finance")
    assert _ids(api.client.get("/issues")) == ["ai-1"]
    assert _ids(api.client.get("/issues", params={"topic": "ai"})) == ["ai-1"]
    r = api.client.get("/issues/ai-1")
    assert r.status_code == 200 and "ai one" in r.text and "Content-Security-Policy" in r.headers


def test_a_launched_topics_archive(monkeypatch):
    api = make_api(monkeypatch, runs=RUNS, public_topics="finance")
    assert _ids(api.client.get("/issues", params={"topic": "finance"})) == ["fin-2", "fin-1"]
    assert "finance one" in api.client.get("/issues/fin-1").text
    assert api.client.get("/issues/fin-dbg").status_code == 404


def test_a_hidden_topics_archive_is_a_404_except_for_admins(monkeypatch):
    api = make_api(monkeypatch, runs=RUNS)
    for headers in ({}, AS_READER):
        assert api.client.get("/issues", params={"topic": "finance"}, headers=headers).status_code == 404
        assert api.client.get("/issues/fin-1", headers=headers).status_code == 404
    assert _ids(api.client.get("/issues", params={"topic": "finance"}, headers=AS_ADMIN)) == ["fin-2", "fin-1"]
    assert api.client.get("/issues/fin-1", headers=AS_ADMIN).status_code == 200


def test_unknown_topic_is_a_404(monkeypatch):
    api = make_api(monkeypatch, runs=RUNS)
    assert api.client.get("/issues", params={"topic": "nope"}, headers=AS_ADMIN).status_code == 404


def test_is_archived_release_per_topic():
    assert run_kind.is_archived_release(RUNS["ai-1"]) and not run_kind.is_archived_release(RUNS["fin-1"])
    assert run_kind.is_archived_release(RUNS["fin-1"], "finance")
    assert not run_kind.is_archived_release(RUNS["fin-dbg"], "finance")
    assert not run_kind.is_archived_release(RUNS["ai-1"], "finance")
