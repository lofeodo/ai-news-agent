from types import SimpleNamespace as NS

import usage_archive
from fakes_firestore import FakeDb


def _run(agent, run_id, inp, out, cost):
    return NS(tags=[f"agent:{agent}", f"run:{run_id}"], prompt_tokens=inp, completion_tokens=out, total_cost=cost)


class FakeLS:
    def __init__(self, runs, reject_select=False):
        self.runs, self.reject_select, self.calls = runs, reject_select, []

    def list_runs(self, **kw):
        self.calls.append(kw)
        if self.reject_select and "select" in kw:
            raise ValueError("bad select field")
        tag = kw["filter"].split('"')[1]
        return [r for r in self.runs if tag in r.tags]


def test_fetch_aggregates_per_agent_and_total():
    ls = FakeLS([_run("agent1a", "r1", 100, 10, 0.001), _run("agent1a", "r1", 200, 20, 0.002),
                 _run("agent3", "r1", 50, 5, None), _run("agent3", "other", 999, 99, 9.0)])
    u = usage_archive.fetch_run_usage(ls, "r1")
    assert u["agents"]["agent1a"] == {"input": 300, "output": 30, "calls": 2, "cost": 0.003, "calls_without_cost": 0}
    assert u["agents"]["agent3"]["calls_without_cost"] == 1
    assert u["total"]["input"] == 350 and u["total"]["calls"] == 3
    assert ls.calls[0]["run_type"] == "llm"


def test_no_traced_calls_returns_none_and_writes_nothing():
    db = FakeDb({"pipeline_runs": {"r1": {"started_at": "x"}}})
    assert usage_archive.archive_run(db, FakeLS([]), "r1") is None
    assert "llm_usage" not in db.store["pipeline_runs"]["r1"]


def test_archive_writes_once_and_is_idempotent():
    db = FakeDb({"pipeline_runs": {"r1": {"started_at": "x"}}})
    ls = FakeLS([_run("agent2a", "r1", 10, 1, 0.1)])
    first = usage_archive.archive_run(db, ls, "r1")
    assert db.store["pipeline_runs"]["r1"]["llm_usage"] == first
    n_calls = len(ls.calls)
    assert usage_archive.archive_run(db, ls, "r1") == first and len(ls.calls) == n_calls  # no second query


def test_select_rejection_falls_back_to_unselected_query():
    ls = FakeLS([_run("agent1b", "r1", 5, 1, 0.0)], reject_select=True)
    assert usage_archive.fetch_run_usage(ls, "r1")["total"]["input"] == 5
    assert "select" not in ls.calls[-1]


def test_catch_up_survives_one_failure():
    db = FakeDb({"pipeline_runs": {"a": {}, "b": {}}})

    class Flaky(FakeLS):
        def list_runs(self, **kw):
            if '"run:a"' in kw["filter"].replace("run:a", '"run:a"'):
                raise RuntimeError("langsmith down")
            return super().list_runs(**kw)

    out = usage_archive.catch_up(db, Flaky([_run("agent1a", "b", 1, 1, 0.0)]), [("a", {}), ("b", {})])
    assert out["a"] is None and out["b"]["total"]["calls"] == 1
