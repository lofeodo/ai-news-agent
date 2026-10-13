"""agent3 leaves an already composed run alone unless forced; main.py accepts ?force=1 for agent3 only."""
import pytest
from fastapi.testclient import TestClient

import agent3_compose as a3
import main
from fakes_firestore import FakeDb


class _Stop(Exception):
    pass


@pytest.fixture
def cloud(monkeypatch):
    import google.cloud.firestore as fs
    db = FakeDb()
    monkeypatch.setattr(fs, "Client", lambda project=None: db)
    monkeypatch.setattr(a3, "USE_FIRESTORE", True)

    def stop(*a, **k):  # the first step after the guard: reaching it means the run went on to compose
        raise _Stop()
    monkeypatch.setattr(a3.tracing, "make_client", stop)
    return db


def _doc(**extra):
    return {"paper_summaries": [], "news_summaries": {}, **extra}


def test_composed_run_is_skipped_without_an_error_record(cloud):
    cloud.store["pipeline_runs"] = {"R1": _doc(newsletter_composed=True)}
    a3.run("R1")  # returns quietly: nothing composed, nothing raised
    assert "agent3_error" not in cloud.store["pipeline_runs"]["R1"]


def test_uncomposed_run_proceeds(cloud):
    cloud.store["pipeline_runs"] = {"R1": _doc()}
    with pytest.raises(_Stop):
        a3.run("R1")


def test_force_recomposes_a_composed_run(cloud):
    cloud.store["pipeline_runs"] = {"R1": _doc(newsletter_composed=True)}
    with pytest.raises(_Stop):
        a3.run("R1", force=True)


def test_force_on_shrunk_pools_warns(cloud, capsys):
    cloud.store["pipeline_runs"] = {"R1": _doc(newsletter_composed=True, news_filtered={"pruned_by_agent3": True})}
    with pytest.raises(_Stop):
        a3.run("R1", force=True)
    assert "Re-run agent2a and agent2b first" in capsys.readouterr().out


# --- main.py ---------------------------------------------------------------------------------------

@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_run_agent", lambda *a: calls.append(a))
    return calls


def _post(monkeypatch, agent, query):
    monkeypatch.setenv("AGENT_NAME", agent)
    return TestClient(main.app).post("/" + query, json={})


def test_force_is_passed_to_agent3(monkeypatch, started):
    assert _post(monkeypatch, "agent3", "?force=1").status_code == 200
    assert started[0][3] == {"force": True}


def test_agent3_without_force_gets_no_options(monkeypatch, started):
    assert _post(monkeypatch, "agent3", "").status_code == 200
    assert started[0][3] is None


def test_other_force_values_are_refused(monkeypatch, started):
    assert _post(monkeypatch, "agent3", "?force=yes").status_code == 400
    assert started == []
