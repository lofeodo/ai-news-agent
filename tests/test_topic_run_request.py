"""?topic= on the orchestrator: validated, written on the run doc (non-AI only), debug-only for non-AI topics."""
import pytest
from fastapi.testclient import TestClient

import main
import orchestrator
import topics
from fakes_firestore import FakeDb


@pytest.fixture
def cloud(monkeypatch):
    import google.cloud.firestore as fs
    db = FakeDb()
    monkeypatch.setattr(fs, "Client", lambda project=None: db)
    published = []
    monkeypatch.setattr(orchestrator, "publish_pubsub", lambda topic, payload: published.append((topic, payload)))
    db.published = published
    return db


@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_run_agent", lambda *a: calls.append(a))
    return calls


@pytest.fixture
def other_topic(monkeypatch):
    """A second, non-AI topic id without needing its real profile to exist yet."""
    real = topics.get_topic
    fake = topics.parse_topic({
        "id": "demo", "name": "Demo", "tagline": "t", "audience": "a", "accent_color": "#112233",
        "languages": ["en"], "variants": ["0_0"], "spotlight": "none",
        "categories": [{"name": "One", "definition": "d"}],
        "sources": [{"type": "rss", "feeds": [{"name": "f", "url": "https://example.com/f.xml"}]}],
        "prompts": real("ai").prompts,
    })
    monkeypatch.setitem(topics._CACHE, "demo", fake)
    monkeypatch.setattr(topics, "list_topic_ids", lambda: ["ai", "demo"])
    return "demo"


def _post(monkeypatch, query):
    monkeypatch.setenv("AGENT_NAME", "orchestrator")
    return TestClient(main.app).post("/" + query, json={})


# --- check_run_request ------------------------------------------------------------------------------

def test_no_topic_is_always_fine():
    assert topics.check_run_request(None, "release") is None
    assert topics.check_run_request("", "debug") is None


def test_ai_may_be_release_or_debug():
    assert topics.check_run_request("ai", "release") is None
    assert topics.check_run_request("ai", "debug") is None


def test_unknown_topic_refused():
    assert "Unknown topic" in topics.check_run_request("nope", "debug")


def test_other_topic_may_be_release_or_debug(other_topic):
    assert topics.check_run_request(other_topic, "release") is None
    assert topics.check_run_request(other_topic, "debug") is None
    assert topics.check_run_request(other_topic, None) is None


# --- main.py ----------------------------------------------------------------------------------------

def test_topic_is_passed_to_the_orchestrator(monkeypatch, started, other_topic):
    assert _post(monkeypatch, "?topic=demo").status_code == 200
    assert started[0][3] == {"topic": "demo"}


def test_topic_and_kind_are_combined(monkeypatch, started, other_topic):
    assert _post(monkeypatch, "?topic=DEMO&kind=debug").status_code == 200
    assert started[0][3] == {"kind": "debug", "topic": "demo"}


def test_non_ai_release_is_started(monkeypatch, started, other_topic):
    assert _post(monkeypatch, "?topic=demo&kind=release").status_code == 200
    assert started[0][3] == {"kind": "release", "topic": "demo"}


def test_unknown_topic_is_a_400(monkeypatch, started):
    assert _post(monkeypatch, "?topic=nope").status_code == 400
    assert started == []


def test_no_topic_leaves_options_as_before(monkeypatch, started):
    assert _post(monkeypatch, "?kind=release").status_code == 200
    assert started[0][3] == {"kind": "release"}
    assert _post(monkeypatch, "").status_code == 200
    assert started[1][3] is None


def test_ai_release_with_topic_param_still_works(monkeypatch, started):
    assert _post(monkeypatch, "?topic=ai&kind=release").status_code == 200
    assert started[0][3] == {"kind": "release", "topic": "ai"}


def test_topic_is_ignored_for_other_agents(monkeypatch, started):
    monkeypatch.setenv("AGENT_NAME", "agent3")
    assert TestClient(main.app).post("/?topic=nope", json={}).status_code == 200
    assert started[0][3] is None


# --- orchestrator -----------------------------------------------------------------------------------

def test_non_ai_run_doc_carries_the_topic(cloud, other_topic):
    orchestrator.run("R1", topic="demo")
    doc = cloud.store["pipeline_runs"]["R1"]
    assert doc["topic"] == "demo" and doc["run_kind"] == "debug"
    assert cloud.published == [(orchestrator.TOPIC_PIPELINE_START, {"run_id": "R1"})]


@pytest.mark.parametrize("topic", [None, "ai"])
def test_ai_run_doc_is_unchanged(cloud, topic):
    orchestrator.run("R1", kind="release", topic=topic)
    doc = cloud.store["pipeline_runs"]["R1"]
    assert "topic" not in doc
    assert set(doc) == {"run_id", "started_at", "status", "run_kind", "agent2_completions"}
    assert doc["run_kind"] == "release"


def test_orchestrator_starts_a_non_ai_release(cloud, other_topic):
    orchestrator.run("R1", kind="release", topic="demo")
    doc = cloud.store["pipeline_runs"]["R1"]
    assert doc["topic"] == "demo" and doc["run_kind"] == "release"


def test_orchestrator_refuses_an_unknown_topic(cloud):
    with pytest.raises(ValueError):
        orchestrator.run("R1", kind="release", topic="nope")
    assert cloud.store.get("pipeline_runs", {}) == {}
    assert cloud.published == []


def test_run_doc_topic_is_read_back(cloud, other_topic):
    orchestrator.run("R1", topic="demo")
    orchestrator.run("R2")
    assert topics.topic_for_run("R1", use_firestore=True).id == "demo"
    assert topics.topic_for_run("R2", use_firestore=True).id == "ai"
    assert topics.topic_for_run("missing", use_firestore=True).id == "ai"
