"""A run of another topic (newer, composed, even sent) must never be picked as "the latest issue".

Every lookup that orders pipeline_runs by started_at follows the AI newsletter only: agent4, the public
pages, the archive, the health check and the drift baseline.
"""
import pytest

import agent_healthcheck as hc
import drift_history
import run_kind
from fakes_firestore import FakeDb

COLLECTION = "pipeline_runs"


def _run(started, topic=None, kind="release", composed=True, sent=False, **extra):
    doc = {"started_at": started, "run_kind": kind, "newsletter_composed": composed,
           "newsletter_subject": f"Issue {started}", **extra}
    if topic:
        doc["topic"] = topic
    if sent:
        doc["agent4_send_summary"] = {"sent": 3, "total": 3}
    return doc


def _db(runs: dict) -> FakeDb:
    return FakeDb({COLLECTION: runs})


# The AI run is older than every topic run below.
def _mixed(n_topic_runs=3, **topic_extra) -> FakeDb:
    runs = {"ai-run": _run("2026-10-11T12:00:00Z", sent=True)}
    for i in range(n_topic_runs):
        runs[f"fin-{i}"] = _run(f"2026-10-12T0{i % 10}:00:00Z", topic="finance", kind="debug", **topic_extra)
    return _db(runs)


# --- run_kind.latest_run ------------------------------------------------------------------------

def test_latest_release_ignores_newer_topic_runs():
    assert run_kind.latest_run(_mixed(), COLLECTION).id == "ai-run"


def test_latest_sent_ignores_newer_topic_runs():
    assert run_kind.latest_run(_mixed(), COLLECTION, sent_only=True).id == "ai-run"


def test_latest_including_debug_ignores_newer_topic_runs():
    """agent4-test (TEST_SEND_TO) and the admin 'Debug run' toggle look at debug runs too."""
    assert run_kind.latest_run(_mixed(), COLLECTION, release_only=False).id == "ai-run"


def test_topic_runs_marked_release_are_still_skipped():
    """Cannot happen through the orchestrator, but the lookup must not rely on that."""
    db = _db({"ai-run": _run("2026-10-11T12:00:00Z", sent=True),
              "fin": _run("2026-10-12T12:00:00Z", topic="finance", kind="release", sent=True)})
    assert run_kind.latest_run(db, COLLECTION).id == "ai-run"
    assert run_kind.latest_run(db, COLLECTION, sent_only=True).id == "ai-run"


def test_a_burst_of_topic_debug_runs_does_not_hide_the_ai_run():
    db = _mixed(n_topic_runs=40)
    assert run_kind.latest_run(db, COLLECTION).id == "ai-run"
    assert run_kind.latest_run(db, COLLECTION, release_only=False).id == "ai-run"


def test_no_ai_run_at_all_gives_none():
    db = _db({"fin": _run("2026-10-12T12:00:00Z", topic="finance", kind="debug")})
    assert run_kind.latest_run(db, COLLECTION) is None
    assert run_kind.latest_run(db, COLLECTION, release_only=False) is None


def test_old_runs_without_a_topic_field_are_ai():
    db = _db({"old": {"started_at": "2026-06-01T10:00:00Z", "newsletter_composed": True}})
    assert run_kind.latest_run(db, COLLECTION).id == "old"


# --- latest_run for one topic (admin preview) ---------------------------------------------------

def test_latest_run_for_a_topic_returns_its_newest_run_debug_included():
    db = _mixed()
    assert run_kind.latest_run(db, COLLECTION, release_only=False, topic="finance").id == "fin-2"


def test_latest_run_for_a_topic_with_no_runs_is_none():
    assert run_kind.latest_run(_mixed(), COLLECTION, release_only=False, topic="gaming") is None


def test_other_topics_have_no_release_lookups():
    with pytest.raises(ValueError):
        run_kind.latest_run(_mixed(), COLLECTION, topic="finance")
    with pytest.raises(ValueError):
        run_kind.latest_run(_mixed(), COLLECTION, release_only=False, sent_only=True, topic="finance")


# --- archive ------------------------------------------------------------------------------------

def test_archive_lists_only_ai_releases():
    db = _db({"ai-run": _run("2026-10-11T12:00:00Z"),
              "fin": _run("2026-10-12T12:00:00Z", topic="finance", kind="release")})
    assert [r["run_id"] for r in run_kind.list_releases(db, COLLECTION)] == ["ai-run"]
    assert not run_kind.is_archived_release(db.store[COLLECTION]["fin"])


# --- health check -------------------------------------------------------------------------------

def test_healthcheck_follows_the_ai_run_not_a_newer_topic_run():
    db = _db({"ai-run": _run("2026-10-11T12:00:00Z"),
              **{f"fin-{i}": _run(f"2026-10-12T{i:02d}:00:00Z", topic="finance", kind="debug") for i in range(20)}})
    assert hc._latest_run_doc(db)[0] == "ai-run"


# --- drift baseline -----------------------------------------------------------------------------

def _drift_run(started, topic=None):
    doc = {"started_at": started,
           "agent1b_review_summary": {"confidence_hist": {"5": 1}, "category_counts": {"A": 1}, "articles": 1}}
    if topic:
        doc["topic"] = topic
    return doc


def test_drift_history_skips_other_topics():
    db = _db({
        "current": _drift_run("2026-10-18T12:00:00Z"),
        "fin": _drift_run("2026-10-17T12:00:00Z", topic="finance"),
        "ai-prev": _drift_run("2026-10-11T12:00:00Z"),
        "ai-older": _drift_run("2026-10-04T12:00:00Z"),
    })
    assert [i for i, _ in drift_history.recent_runs(db, "current", 2)] == ["ai-prev", "ai-older"]
    assert [s["run_id"] for s in drift_history.load_baseline(db, "current", 2)] == ["ai-prev", "ai-older"]


def test_drift_history_still_returns_n_runs_after_a_burst_of_topic_runs():
    runs = {"current": _drift_run("2026-10-18T12:00:00Z"),
            "ai-prev": _drift_run("2026-10-11T12:00:00Z")}
    for i in range(30):
        runs[f"fin-{i}"] = _drift_run(f"2026-10-12T{i:02d}:00:00Z", topic="finance")
    db = _db(runs)
    assert [i for i, _ in drift_history.recent_runs(db, "current", 1)] == ["ai-prev"]
    assert [s["run_id"] for s in drift_history.load_baseline(db, "current", 1)] == ["ai-prev"]
