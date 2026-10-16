"""Five topics now run on Sunday morning and can land in one container: a run's watchdog must stop that run only."""
import subprocess
import sys

import pytest

import main

KILLED = -9


class _Timer:
    """Captures the watchdog callback instead of starting a thread."""

    def __init__(self, timeout, fn):
        self.fn = fn
        _Timer.last = self

    def start(self):
        pass

    def cancel(self):
        pass


@pytest.fixture
def env(monkeypatch):
    recorded, exits, popens = [], [], []
    monkeypatch.setattr(main.threading, "Timer", _Timer)
    monkeypatch.setattr(main, "_record_failure", lambda agent, run_id, error: recorded.append((agent, run_id, error)))
    monkeypatch.setattr(main.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr("time.sleep", lambda s: None)

    class Proc:
        def __init__(self, args, **kw):
            popens.append(args)
            self.killed = False

        def kill(self):
            self.killed = True

        def wait(self):
            _Timer.last.fn()          # the deadline passes while the child runs
            return KILLED if self.killed else 0

    monkeypatch.setattr(subprocess, "Popen", Proc)
    return recorded, exits, popens


def test_every_pipeline_agent_runs_in_its_own_child():
    assert {"agent1a", "agent1b", "agent2a", "agent2b", "agent3", "agent4"} <= main._CHILD_PROCESS_AGENTS


@pytest.mark.parametrize("agent", ["agent1b", "agent2b"])
def test_a_timeout_kills_only_this_runs_child(env, agent):
    recorded, exits, popens = env
    main._run_agent(agent, main.AGENT_REGISTRY[agent], "R1")
    assert exits == []                                     # the process (and any other run in it) survives
    assert len(popens) == 1                                # a killed attempt is not retried, even for agent2b
    assert len(recorded) == 1 and recorded[0][:2] == (agent, "R1") and "hard timeout" in recorded[0][2]


def test_an_in_process_agent_still_hard_exits(env, monkeypatch):
    recorded, exits, _ = env
    import types
    fake = types.SimpleNamespace(run=lambda run_id, **kw: _Timer.last.fn())
    monkeypatch.setitem(sys.modules, "agent_healthcheck", fake)
    main._run_agent("healthcheck", "agent_healthcheck", "R1")
    assert exits == [124] and "hard timeout" in recorded[0][2]


def test_options_reach_the_child(monkeypatch):
    popens = []

    class Proc:
        def __init__(self, args, **kw):
            popens.append(args)

        def wait(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Proc)
    main._run_isolated("agent3", "agent3_compose", "R1", 0, {"proc": None}, {"force": True})
    assert popens[0][-2:] == ["R1", '{"force": true}']


def test_child_code_passes_run_id_and_options(tmp_path):
    (tmp_path / "probe_agent.py").write_text(
        "def run(run_id, **kw):\n"
        "    open(__file__ + '.out', 'w').write(f'{run_id} {sorted(kw.items())}')\n")
    subprocess.run([sys.executable, "-c", main._CHILD_CODE, str(tmp_path), "probe_agent", "R9",
                    '{"topic": "finance"}'], check=True)
    assert (tmp_path / "probe_agent.py.out").read_text() == "R9 [('topic', 'finance')]"
