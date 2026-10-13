"""The agent2a/agent2b completion counter: each stage counts once per run, only the second finisher triggers."""
import pytest

import agent2a_summarize_papers as a2a
import agent2b_summarize_news as a2b
import stage_counter


class _Ref:
    def __init__(self, data):
        self.data = data

    def get(self, transaction=None):
        return type("S", (), {"to_dict": lambda s: dict(self.data)})()


class _Txn:
    def update(self, ref, fields):
        ref.data.update(fields)


@pytest.fixture
def doc(monkeypatch):
    import google.cloud.firestore as fs
    ref = _Ref({})
    db = type("D", (), {"collection": lambda s, n: type("C", (), {"document": lambda s2, i: ref})(),
                        "transaction": lambda s: _Txn()})()
    monkeypatch.setattr(fs, "Client", lambda project=None: db)
    monkeypatch.setattr(fs, "transactional", lambda f: f)
    return ref


def test_a_repeated_agent2a_counts_once_and_cannot_reach_two(doc):
    assert a2a.increment_and_check("R1") is False
    assert a2a.increment_and_check("R1") is False
    assert doc.data["agent2_completions"] == 1 and doc.data["agent2a_counted"] is True


def test_the_second_finisher_triggers_exactly_once(doc):
    assert a2a.increment_and_check("R1") is False
    assert a2b.increment_and_check("R1") is True
    assert doc.data["agent2_completions"] == 2
    # repeats of either stage afterwards never trigger agent3 again
    assert a2b.increment_and_check("R1") is False
    assert a2a.increment_and_check("R1") is False
    assert doc.data["agent2_completions"] == 2


def test_order_does_not_matter(doc):
    assert a2b.increment_and_check("R1") is False
    assert a2a.increment_and_check("R1") is True


def test_a_run_in_flight_without_flags_still_reaches_two(doc):
    doc.data["agent2_completions"] = 1
    assert a2a.increment_and_check("R1") is True


def test_count_stage_sets_the_given_flag(doc):
    assert stage_counter.count_stage("R1", "x_counted") is False
    assert doc.data["x_counted"] is True
