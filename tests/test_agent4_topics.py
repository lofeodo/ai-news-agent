"""agent4 sends any newsletter: ?topic=<id> picks the run and the subscribers; the AI send is unchanged."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import agent4_send as a4
import main
from fakes_firestore import FakeDb

NOW = datetime.now(timezone.utc)
HTML = '<p><a href="{{UNSUBSCRIBE_URL}}">u</a> <a href="{{PREFERENCES_URL}}">p</a></p>'


def _run(hours_ago, topic=None, kind="release"):
    doc = {"started_at": (NOW - timedelta(hours=hours_ago)).isoformat(), "run_kind": kind,
           "newsletter_composed": True, "newsletter_subject": f"Issue {topic or 'ai'}",
           "newsletter_variants": {"0_0": HTML + (topic or "ai")}}
    if topic:
        doc["topic"] = topic
    return doc


def _runs(**docs):
    """Run docs keyed by id, each carrying its own id as `run_id` (as the orchestrator writes it)."""
    return {run_id: {**d, "run_id": run_id} for run_id, d in docs.items()}


SUBSCRIBERS = {
    "ai@example.com":   {"email": "ai@example.com", "token": "t-ai", "active": True, "uid": "u1"},
    "both@example.com": {"email": "both@example.com", "token": "t-both", "active": True,
                         "newsletters": ["ai", "finance"], "subscribed_any": True},
    "fin@example.com":  {"email": "fin@example.com", "token": "t-fin", "active": False,
                         "newsletters": ["finance"], "subscribed_any": True, "uid": "u3"},
    "gone@example.com": {"email": "gone@example.com", "token": "t-gone", "active": False, "newsletters": []},
}


@pytest.fixture
def env(monkeypatch):
    db = FakeDb({"pipeline_runs": _runs(**{"ai-run": _run(19), "fin-run": _run(20, "finance"),
                                           "fin-debug": _run(2, "finance", kind="debug")}),
                 "subscribers": {k: dict(v) for k, v in SUBSCRIBERS.items()}})
    sends, configs_loaded = [], []
    import google.cloud.firestore as fs
    monkeypatch.setattr(fs, "Client", lambda project=None: db)
    monkeypatch.setattr(a4, "USE_FIRESTORE", True)
    monkeypatch.setattr(a4, "TEST_SEND_TO", "")
    monkeypatch.setattr(a4, "CLICK_TRACKING", False)
    monkeypatch.setattr(a4, "SERVICE_BASE_URL", "https://api.test")
    monkeypatch.setattr(a4, "FRONTEND_BASE_URL", "https://site.test")
    monkeypatch.setattr(a4, "_get_sendgrid_api_key", lambda: "k")
    monkeypatch.setattr(a4, "_load_user_section_configs", lambda d, s: configs_loaded.append(s) or {})
    monkeypatch.setattr(a4, "send_email", lambda key, to, html, subj, click_run_id=None, **kw:
                        sends.append({"to": to, "html": html, "subject": subj, **kw}))
    return db, sends, configs_loaded


def test_a_finance_send_mails_only_finance_subscribers_with_finance_links(env):
    db, sends, configs_loaded = env
    a4.run("agent4-inv", topic="finance")
    assert sorted(s["to"] for s in sends) == ["both@example.com", "fin@example.com"]
    fin = next(s for s in sends if s["to"] == "fin@example.com")
    assert "https://api.test/unsubscribe?token=t-fin&n=finance" in fin["html"]
    assert "https://site.test/preferences.html?token=t-fin&n=finance" in fin["html"]
    assert fin["html"].endswith("finance") and fin["subject"] == "Issue finance"
    assert fin["sender_name"] == "Markets & Money Weekly"
    assert configs_loaded == []                                   # the premium section reorder is AI-only
    summary = db.store["pipeline_runs"]["fin-run"]["agent4_send_summary"]
    assert summary["sent"] == 2 and summary["total"] == 2
    assert set(db.store["pipeline_runs/fin-run/sent"]) == {a4._recipient_id("both@example.com"),
                                                           a4._recipient_id("fin@example.com")}
    assert "agent4_send_summary" not in db.store["pipeline_runs"]["ai-run"]


def test_the_ai_send_is_unchanged_by_topic_runs_and_subscribers(env):
    db, sends, configs_loaded = env
    a4.run("agent4-inv")
    assert sorted(s["to"] for s in sends) == ["ai@example.com", "both@example.com"]
    ai = next(s for s in sends if s["to"] == "ai@example.com")
    assert '<a href="https://api.test/unsubscribe?token=t-ai">' in ai["html"] and "&n=" not in ai["html"]
    assert "sender_name" not in ai and ai["html"].endswith("ai")
    assert len(configs_loaded) == 1
    assert "agent4_send_summary" in db.store["pipeline_runs"]["ai-run"]


def test_a_finance_send_refuses_a_stale_finance_issue(env):
    db, sends, _ = env
    db.store["pipeline_runs"]["fin-run"]["started_at"] = (NOW - timedelta(days=7)).isoformat()
    with pytest.raises(a4.StaleNewsletterError):
        a4.run("agent4-inv", topic="finance")
    assert sends == [] and "agent4_error" in db.store["pipeline_runs"]["fin-run"]


def test_test_send_takes_that_topics_newest_run_debug_included(env, monkeypatch):
    db, sends, _ = env
    monkeypatch.setattr(a4, "TEST_SEND_TO", "owner@example.com")
    a4.run("agent4-inv", topic="finance")
    assert [s["to"] for s in sends] == ["owner@example.com"]
    assert "&n=finance" in sends[0]["html"]
    assert db.store["pipeline_runs"]["fin-debug"]["agent4_send_summary"]["sent"] == 1


def test_a_topic_with_no_run_fails_loudly(env):
    with pytest.raises(RuntimeError):
        a4.run("agent4-inv", topic="gaming")


def test_the_subscriber_query_for_a_topic():
    db = FakeDb({"subscribers": {k: dict(v) for k, v in SUBSCRIBERS.items()}})
    assert sorted(s["email"] for s in a4._active_subscribers(db, "finance")) == ["both@example.com", "fin@example.com"]
    assert sorted(s["email"] for s in a4._active_subscribers(db)) == ["ai@example.com", "both@example.com"]


# --- main.py ----------------------------------------------------------------------------------------

@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setenv("AGENT_NAME", "agent4")
    monkeypatch.setattr(main.threading, "Thread", lambda target, args, daemon: type(
        "T", (), {"start": lambda self: calls.append(args)})())
    return calls


def test_main_passes_the_topic_to_agent4(started):
    client = TestClient(main.app)
    assert client.post("/?topic=Finance", json={}).status_code == 200
    assert started[0][3] == {"topic": "finance"}
    assert client.post("/", json={}).status_code == 200
    assert started[1][3] is None                                   # no topic: the AI send, exactly as before


def test_main_refuses_an_unknown_topic_for_agent4(started):
    r = TestClient(main.app).post("/?topic=nope", json={})
    assert r.status_code == 400 and started == []
