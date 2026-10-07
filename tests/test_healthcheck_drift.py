from datetime import datetime, timezone

import pytest

import agent_healthcheck as hc
from fakes_firestore import FakeDb

SUMMARY = {"mode": "graph", "articles": 100, "routed_to_review": 30,
           "confidence_hist": {"1": 1, "2": 4, "3": 10, "4": 50, "5": 35, "none": 0},
           "category_counts": {"A": 50, "B": 30, "C": 20}}


def _healthy_doc(started_at):
    doc = {"started_at": started_at, "agent1b_review_summary": SUMMARY}
    for field, _ in hc.EXPECTED_STAGES:
        doc[field] = {"ok": True}
    return doc


def _setup(monkeypatch, runs):
    db = FakeDb({"pipeline_runs": runs})
    sent = []
    import google.cloud.firestore as fs
    monkeypatch.setattr(fs, "Client", lambda project=None: db)
    monkeypatch.setattr(hc, "USE_FIRESTORE", True)
    monkeypatch.setattr(hc, "_notify", lambda message, healthy, mode="send": sent.append((message, healthy)))
    return db, sent


def _runs(n_prior):
    now = datetime.now(timezone.utc).isoformat()
    runs = {"cur": _healthy_doc(now)}
    for i in range(n_prior):
        runs[f"p{i}"] = _healthy_doc(f"2026-09-{i + 1:02d}T06:00:00+00:00")
    return runs


def test_drift_section_in_healthy_email_without_changing_status(monkeypatch):
    _, sent = _setup(monkeypatch, _runs(4))
    hc._run("t")
    (message, healthy), = sent
    assert healthy is True and "Drift check" in message and "no drift" in message


def test_insufficient_history_is_reported(monkeypatch):
    _, sent = _setup(monkeypatch, _runs(1))
    hc._run("t")
    assert "not enough history" in sent[0][0] and sent[0][1] is True


def test_drift_failure_does_not_suppress_heartbeat_or_change_status(monkeypatch):
    _, sent = _setup(monkeypatch, _runs(4))
    import drift_history
    monkeypatch.setattr(drift_history, "load_baseline", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    hc._run("t")
    (message, healthy), = sent
    assert healthy is True and "Drift check: failed" in message and "boom" in message


def test_drift_flag_does_not_flip_status(monkeypatch):
    runs = _runs(4)
    shifted = dict(SUMMARY, routed_to_review=90,
                   confidence_hist={"1": 40, "2": 30, "3": 20, "4": 8, "5": 2, "none": 0},
                   category_counts={"A": 5, "B": 5, "C": 90})
    runs["cur"]["agent1b_review_summary"] = shifted
    _, sent = _setup(monkeypatch, runs)
    hc._run("t")
    (message, healthy), = sent
    assert healthy is True and "DRIFT FLAGGED" in message


def test_pipeline_problems_still_reported_with_drift_section(monkeypatch):
    runs = _runs(4)
    runs["cur"]["agent3_error"] = "boom"
    _, sent = _setup(monkeypatch, runs)
    hc._run("t")
    (message, healthy), = sent
    assert healthy is False and "agent3 failed" in message and "Drift check" in message
