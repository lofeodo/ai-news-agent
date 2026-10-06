"""Healthcheck judge section: report-only by default, never suppresses the heartbeat."""
from types import SimpleNamespace

import pytest

import agent_healthcheck as hc
import online_judge
import summary_sources as ss
from test_healthcheck_drift import _runs, _setup


def _verdict(ok):
    return SimpleNamespace(type="tool_use", name="record_verdict",
                           input={"supported": ok, "unsupported_claims": [] if ok else ["x"]})


class Client:
    def __init__(self, ok):
        self.messages, self.ok = self, ok

    def create(self, **kw):
        return SimpleNamespace(content=[_verdict(self.ok)], usage=SimpleNamespace(input_tokens=500, output_tokens=40))


def _with_summaries(runs, run_id, db=None):
    doc = runs[run_id]
    doc["paper_summaries"] = [{"id": "p1", "title": "P", "summary": "s", "used_fallback": False}]
    doc["news_summaries"] = {"A": [{"url": f"http://n/{i}", "title": "T", "summary": "s", "used_fallback": False} for i in range(5)]}


def _judge_env(monkeypatch, ok=True):
    monkeypatch.setenv("ANTHROPIC_1ST_API_KEY", "k")
    import tracing
    monkeypatch.setattr(tracing, "make_client", lambda *a, **k: Client(ok))


def _prior_with_judge(runs, unsupported):
    for k, d in runs.items():
        if k != "cur":
            d["judge_results"] = {"items": [], "judged": 12, "unsupported": unsupported}


def test_skipped_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_1ST_API_KEY", raising=False)
    _, sent = _setup(monkeypatch, _runs(4))
    hc._run("t")
    assert "Summary faithfulness: skipped" in sent[0][0] and sent[0][1] is True


def test_report_includes_rate_and_caps_and_stays_healthy(monkeypatch):
    _judge_env(monkeypatch, ok=True)
    runs = _runs(4)
    _with_summaries(runs, "cur")
    db, sent = _setup(monkeypatch, runs)
    ss.save_sources(_batchdb(db), "cur", "paper", [{"ident": "p1", "text": "src words"}])
    ss.save_sources(_batchdb(db), "cur", "news", [{"ident": f"http://n/{i}", "text": "src words"} for i in range(5)])
    hc._run("t")
    msg, healthy = sent[0]
    assert healthy is True and "Summary faithfulness" in msg and "unsupported: 0/6" in msg
    assert "judge_results" in db.store["pipeline_runs"]["cur"]


def _batchdb(db):
    from test_summary_sources import Batch

    class W:
        def collection(self, n):
            return db.collection(n)

        def batch(self):
            return Batch(self)
    return W()


def test_drift_flag_is_report_only_by_default(monkeypatch):
    _judge_env(monkeypatch, ok=False)
    runs = _runs(4)
    _prior_with_judge(runs, 0)
    _with_summaries(runs, "cur")
    db, sent = _setup(monkeypatch, runs)
    ss.save_sources(_batchdb(db), "cur", "paper", [{"ident": "p1", "text": "t"}])
    ss.save_sources(_batchdb(db), "cur", "news", [{"ident": f"http://n/{i}", "text": "t"} for i in range(5)])
    # make the prior sample big enough for Fisher to reach significance against a 6/6 unsupported week
    for k, d in runs.items():
        if k != "cur":
            d["judge_results"]["judged"] = 40
    hc._run("t")
    msg, healthy = sent[0]
    assert "DRIFT" in msg and healthy is True


def test_drift_flag_alerts_only_when_enabled(monkeypatch):
    monkeypatch.setattr(hc.config, "JUDGE_ALERTING_ENABLED", True)
    _judge_env(monkeypatch, ok=False)
    runs = _runs(4)
    _prior_with_judge(runs, 0)
    for k, d in runs.items():
        if k != "cur":
            d["judge_results"]["judged"] = 40
    _with_summaries(runs, "cur")
    db, sent = _setup(monkeypatch, runs)
    ss.save_sources(_batchdb(db), "cur", "paper", [{"ident": "p1", "text": "t"}])
    ss.save_sources(_batchdb(db), "cur", "news", [{"ident": f"http://n/{i}", "text": "t"} for i in range(5)])
    hc._run("t")
    msg, healthy = sent[0]
    assert healthy is False and "summary faithfulness dropped" in msg


def test_judge_failure_does_not_suppress_heartbeat(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_1ST_API_KEY", "k")
    _, sent = _setup(monkeypatch, _runs(4))
    monkeypatch.setattr(online_judge, "judge_run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    import tracing
    monkeypatch.setattr(tracing, "make_client", lambda *a, **k: object())
    hc._run("t")
    (msg, healthy), = sent
    assert healthy is True and "Summary faithfulness: failed" in msg and "boom" in msg
