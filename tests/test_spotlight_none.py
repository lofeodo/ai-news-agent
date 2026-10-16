"""A topic with spotlight "none": agent1a and agent2a still record their stage, count themselves and publish,
so the Pub/Sub chain, the agent2 counter and agent3's trigger work exactly as for the AI newsletter."""
import json

import pytest

import agent1a_fetch_papers as a1a
import agent2a_summarize_papers as a2a
import agent3_compose as a3
import stage_counter
from fakes_firestore import FakeDb


class _Pub:
    """Stand-in for pubsub_v1.PublisherClient that records what is published."""
    log: list = []

    def topic_path(self, project, topic):
        return topic

    def publish(self, topic, data):
        _Pub.log.append((topic, json.loads(data)))

        class _F:
            def result(self, timeout=None):
                return "id"
        return _F()


@pytest.fixture
def cloud(monkeypatch, demo_topic):
    import google.cloud.firestore as fs
    import google.cloud.pubsub_v1 as ps
    db = FakeDb({"pipeline_runs": {"R1": {"topic": "demo", "agent2_completions": 0}}})
    _Pub.log = []
    monkeypatch.setattr(fs, "Client", lambda project=None: db)
    monkeypatch.setattr(ps, "PublisherClient", _Pub)
    for mod in (a1a, a2a, a3):
        monkeypatch.setattr(mod, "USE_FIRESTORE", True)
    monkeypatch.setattr(stage_counter, "mark_published", lambda run_id: db.store["pipeline_runs"][run_id].update(
        {stage_counter.PUBLISHED_FLAG: True}))
    # the stage counter uses a Firestore transaction the fake does not have: count in plain code
    def count(run_id, flag):
        doc = db.store["pipeline_runs"][run_id]
        if doc.get(flag):
            return False
        doc[flag] = True
        doc["agent2_completions"] = doc.get("agent2_completions", 0) + 1
        return doc["agent2_completions"] >= 2
    monkeypatch.setattr(stage_counter, "count_stage", count)
    return db


def test_agent1a_records_an_empty_stage_and_publishes(cloud, monkeypatch):
    monkeypatch.setattr(a1a, "select_trending_papers", lambda *a, **k: pytest.fail("must not fetch papers"))
    monkeypatch.setattr(a1a, "fetch_papers", lambda *a, **k: pytest.fail("must not fetch papers"))
    a1a.run("R1")
    doc = cloud.store["pipeline_runs"]["R1"]
    assert doc["scored_papers"] == [] and doc["paper_candidates"] == []
    assert doc["agent1a_skipped"] == "no spotlight for topic"
    assert doc["topic"] == "demo" and "agent1a_error" not in doc
    assert _Pub.log == [(a1a.TOPIC_PAPERS_SCORED, {"run_id": "R1"})]


def test_agent1a_skips_spotlight_history_for_other_topics(cloud, monkeypatch):
    monkeypatch.setattr(a1a.spotlight_history, "record_spotlight", lambda *a, **k: pytest.fail("must not record"))
    monkeypatch.setattr(a1a.spotlight_history, "load_seen_ids", lambda *a, **k: pytest.fail("must not read"))
    a1a.run("R1")


def test_agent2a_summarizes_nothing_and_counts_itself(cloud, monkeypatch):
    monkeypatch.setattr(a2a, "summarize_with_fallback", lambda *a, **k: pytest.fail("must not summarize"))
    a2a.run("R1")
    doc = cloud.store["pipeline_runs"]["R1"]
    assert doc["paper_summaries"] == [] and doc["agent2a_skipped"] == "no spotlight for topic"
    assert doc["agent2a_counted"] is True and doc["agent2_completions"] == 1
    assert _Pub.log == []            # waits for agent2b, as usual


def test_the_second_finisher_publishes_content_summarized(cloud, monkeypatch):
    monkeypatch.setattr(a2a, "summarize_with_fallback", lambda *a, **k: pytest.fail("must not summarize"))
    cloud.store["pipeline_runs"]["R1"].update({"agent2b_counted": True, "agent2_completions": 1})
    a2a.run("R1")
    assert _Pub.log == [(a2a.TOPIC_CONTENT_SUMMARIZED, {"run_id": "R1"})]
    assert cloud.store["pipeline_runs"]["R1"][stage_counter.PUBLISHED_FLAG] is True


def test_local_mode_writes_empty_files(monkeypatch, tmp_path, demo_topic):
    monkeypatch.setenv("TOPIC", "demo")
    for mod in (a1a, a2a):
        monkeypatch.setattr(mod, "USE_FIRESTORE", False)
        monkeypatch.setattr(mod, "DATA_DIR", str(tmp_path))
    a1a.run("local")
    a2a.run("local")
    scored = json.loads((tmp_path / "scored_papers.json").read_text(encoding="utf-8"))
    summaries = json.loads((tmp_path / "paper_summaries.json").read_text(encoding="utf-8"))
    assert scored["top_papers"] == [] and scored["skipped"]
    assert summaries["papers"] == [] and summaries["total_papers"] == 0


# --- agent3 -----------------------------------------------------------------------------------------

class _Stop(Exception):
    pass


def _agent3_doc(topic=None, papers=()):
    doc = {"paper_summaries": list(papers), "news_summaries": {}}
    if topic:
        doc["topic"] = topic
    return doc


def test_agent3_composes_a_no_spotlight_topic_without_a_paper(cloud, monkeypatch):
    cloud.store["pipeline_runs"]["R1"] = _agent3_doc("demo")

    def stop(*a, **k):      # the step after the paper guard: reaching it means the guard let the run through
        raise _Stop()
    monkeypatch.setattr(a3.tracing, "make_client", stop)
    with pytest.raises(_Stop):
        a3.run("R1")


def test_agent3_still_refuses_an_ai_issue_with_no_paper(cloud, monkeypatch):
    cloud.store["pipeline_runs"]["R1"] = _agent3_doc()
    monkeypatch.setattr(a3.tracing, "make_client", lambda *a, **k: pytest.fail("guard did not stop the run"))
    with pytest.raises(RuntimeError, match="no spotlight paper has a summary"):
        a3.run("R1")
    assert "no spotlight paper" in cloud.store["pipeline_runs"]["R1"]["agent3_error"]
