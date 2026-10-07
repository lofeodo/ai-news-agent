from datetime import datetime, timezone
from types import SimpleNamespace as NS

import agent_healthcheck as hc
from fakes_firestore import FakeDb

SUMMARY = {"mode": "graph", "articles": 100, "routed_to_review": 30,
           "confidence_hist": {"1": 1, "2": 4, "3": 10, "4": 50, "5": 35, "none": 0},
           "category_counts": {"A": 50, "B": 30, "C": 20}}


def _usage(inp=1_000_000, out=100_000, cost=1.40):
    return {"source": "langsmith", "agents": {"agent1a": {"input": inp, "output": out, "calls": 5, "cost": cost, "calls_without_cost": 0}},
            "total": {"input": inp, "output": out, "calls": 5, "cost": cost, "calls_without_cost": 0}}


def _doc(started_at, usage=None):
    doc = {"started_at": started_at, "agent1b_review_summary": SUMMARY}
    for field, _ in hc.EXPECTED_STAGES:
        doc[field] = {"ok": True}
    if usage:
        doc["llm_usage"] = usage
    return doc


def _setup(monkeypatch, runs, ls_runs=(), ls_error=None):
    db = FakeDb({"pipeline_runs": runs})
    sent = []
    import google.cloud.firestore as fs
    import langsmith
    monkeypatch.setattr(fs, "Client", lambda project=None: db)

    class FakeLS:
        def list_runs(self, **kw):
            if ls_error:
                raise ls_error
            tag = kw["filter"].split('"')[1]
            return [r for r in ls_runs if tag in r.tags]

    monkeypatch.setattr(langsmith, "Client", lambda *a, **k: FakeLS())
    monkeypatch.setattr(hc, "USE_FIRESTORE", True)
    monkeypatch.setattr(hc, "_notify", lambda message, healthy, mode="send": sent.append((message, healthy)))
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test")
    return db, sent


def _runs(n_prior, prior_usage=True, current_usage=None):
    now = datetime.now(timezone.utc).isoformat()
    runs = {"cur": _doc(now, current_usage)}
    for i in range(n_prior):
        runs[f"p{i}"] = _doc(f"2026-09-{i + 1:02d}T06:00:00+00:00", _usage() if prior_usage else None)
    return runs


def _ls_call(agent, run_id, inp, out, cost):
    return NS(tags=[f"agent:{agent}", f"run:{run_id}"], prompt_tokens=inp, completion_tokens=out, total_cost=cost)


def test_skipped_without_key(monkeypatch):
    _, sent = _setup(monkeypatch, _runs(4))
    monkeypatch.delenv("LANGSMITH_API_KEY")
    hc._run("t")
    assert "Usage check: skipped" in sent[0][0] and sent[0][1] is True


def test_reports_both_costs_archives_and_flags_nothing_when_steady(monkeypatch):
    db, sent = _setup(monkeypatch, _runs(4), [_ls_call("agent1a", "cur", 1_000_000, 100_000, 1.40)])
    hc._run("t")
    msg, healthy = sent[0]
    assert healthy is True
    assert "LLM usage this run: 1,100,000 tokens" in msg
    assert "LangSmith $1.40" in msg and "list-price estimate $1.50" in msg
    assert "no drift" in msg
    assert db.store["pipeline_runs"]["cur"]["llm_usage"]["total"]["input"] == 1_000_000   # archived for history


def test_token_jump_is_flagged_without_changing_status(monkeypatch):
    _, sent = _setup(monkeypatch, _runs(4), [_ls_call("agent1a", "cur", 5_000_000, 500_000, 7.0)])
    hc._run("t")
    msg, healthy = sent[0]
    assert healthy is True and "Usage drift vs prior 4 runs (median): DRIFT FLAGGED" in msg
    assert "total tokens: DRIFT" in msg


def test_thin_history_still_shows_this_runs_numbers(monkeypatch):
    _, sent = _setup(monkeypatch, _runs(1), [_ls_call("agent1a", "cur", 1_000, 100, 0.01)])
    hc._run("t")
    assert "LLM usage this run" in sent[0][0] and "not enough history yet" in sent[0][0]


def test_no_traces_found_is_reported(monkeypatch):
    _, sent = _setup(monkeypatch, _runs(4), [])
    hc._run("t")
    assert "no traced Claude calls found" in sent[0][0]


def test_langsmith_failure_does_not_suppress_heartbeat_or_change_status(monkeypatch):
    _, sent = _setup(monkeypatch, _runs(4), ls_error=RuntimeError("langsmith down"))
    hc._run("t")
    (msg, healthy), = sent
    assert healthy is True and "Usage check: failed" in msg and "langsmith down" in msg
