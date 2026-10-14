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

    def set(self, fields, merge=False):
        self.data.update(fields)


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
    stage_counter.mark_published("R1")
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


def test_a_repeat_publishes_when_both_stages_counted_but_the_publish_never_happened(doc):
    assert a2a.increment_and_check("R1") is False
    assert a2b.increment_and_check("R1") is True        # counted, then the publish failed: no mark_published
    assert a2b.increment_and_check("R1") is True        # the retried attempt publishes
    assert doc.data["agent2_completions"] == 2
    stage_counter.mark_published("R1")
    assert a2b.increment_and_check("R1") is False
    assert a2a.increment_and_check("R1") is False


def test_a_repeat_of_the_first_finisher_does_not_publish_before_the_other_stage(doc):
    assert a2a.increment_and_check("R1") is False
    assert a2a.increment_and_check("R1") is False       # count is 1, nothing to publish yet


def test_mark_published_sets_the_flag(doc):
    stage_counter.mark_published("R1")
    assert doc.data[stage_counter.PUBLISHED_FLAG] is True
