import json

import pytest

import config
from evals import cost, dedup_eval, run_dedup_eval as runner
from tests.fakes import response, tool_use


class RoutingClient:
    """Replies by model, so thread order doesn't matter. `by_model`: model -> groups or an Exception."""

    def __init__(self, by_model):
        self.by_model, self.calls = by_model, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw["model"])
        out = self.by_model[kw["model"]]
        if isinstance(out, Exception):
            raise out
        return response(tool_use("report_duplicates", groups=out))


def _case(cid="c1", kind="real"):
    arts = [{"id": i, "title": f"T{i}", "summary": "s", "hn_score": h}
            for i, h in (("a", 1), ("b", 9), ("c", None), ("d", 2))]
    return {"case_id": cid, "kind": kind, "category": "Industry & Business", "articles": arts}


class Gold:
    groups = {"c1": [frozenset({"a", "b"})]}
    excluded = {}
    borderline = {}


def _prepared():
    return dedup_eval.prepare_cases([_case()], Gold)


HAIKU, SONNET = config.SCORING_MODEL, config.JUDGE_MODEL


def test_run_eval_applies_keep_policy_and_records_usage():
    rows, arts = _prepared()
    client = RoutingClient({HAIKU: [{"indices": [0, 1], "reason": "same"}], SONNET: []})
    runner.run_eval(rows, arts, client, ["control", "haiku", "sonnet"], repeats=2)
    r = rows[0]["runs"]
    assert len(r["haiku"]) == 2 and len(r["sonnet"]) == 2 and len(r["control"]) == 2
    assert r["haiku"][0]["groups"] == [["a", "b"]]
    assert r["haiku"][0]["removed"] == ["a"]          # b has the higher HN score, so it is kept
    assert r["haiku"][0]["input_tokens"] == 10
    assert r["sonnet"][0]["removed"] == [] and r["control"][0]["removed"] == []
    assert client.calls.count(HAIKU) == 2 and client.calls.count(SONNET) == 2


def test_run_eval_records_dedup_error_as_failed_run():
    rows, arts = _prepared()
    runner.run_eval(rows, arts, RoutingClient({HAIKU: RuntimeError("boom")}), ["haiku"], repeats=1)
    run = rows[0]["runs"]["haiku"][0]
    assert run["failed"] is True and run["groups"] == [] and run["removed"] == []


def test_run_eval_output_scores_end_to_end():
    rows, arts = _prepared()
    client = RoutingClient({HAIKU: [{"indices": [0, 1], "reason": "x"}], SONNET: []})
    runner.run_eval(rows, arts, client, ["control", "haiku", "sonnet"], repeats=1)
    metrics, verdict = dedup_eval.score(rows)
    assert metrics["duplicate_recall__haiku"]["value"] == 1.0
    assert metrics["duplicate_recall__sonnet"]["value"] == 0.0
    assert metrics["residual_duplicate_rate__control"]["value"] == 1.0
    assert verdict
    json.dumps(rows)   # rows file must be serializable


def test_total_cost_prices_each_model_separately():
    rows, arts = _prepared()
    runner.run_eval(rows, arts, RoutingClient({HAIKU: [], SONNET: []}), ["haiku", "sonnet"], repeats=1)
    expected = (cost.estimate_cost(10, 5, model=HAIKU) + cost.estimate_cost(10, 5, model=SONNET))
    assert runner.total_cost(rows) == pytest.approx(expected)


def test_estimate_grows_with_repeats_and_skips_control():
    _, arts = _prepared()
    one = runner.estimate_cost(arts, ["haiku"], 1)
    assert runner.estimate_cost(arts, ["haiku"], 3) == pytest.approx(3 * one)
    assert runner.estimate_cost(arts, ["control"], 3) == 0.0
    assert runner.estimate_cost(arts, ["sonnet"], 1) > one


def test_cost_guard_blocks_big_estimates_without_approval():
    with pytest.raises(cost.CostLimitExceeded):
        cost.CostGuard().check(5.0)


def test_dry_run_makes_no_client_and_prints_estimate(monkeypatch, capsys):
    import anthropic

    def boom(*a, **k):
        raise AssertionError("dry run must not create a client")
    monkeypatch.setattr(anthropic, "Anthropic", boom)
    assert runner.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "estimated worst-case cost" in out
    assert "cases: 57" in out
    assert "gold groups: 29" in out


def test_unknown_arm_is_rejected():
    with pytest.raises(SystemExit):
        runner.main(["--dry-run", "--arms", "gpt"])
