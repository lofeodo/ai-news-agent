import pytest

from drift import evaluate_usage, usage_ratio_test, usage_totals

KW = dict(min_runs=3, min_ratio=0.5, min_tokens=100_000, min_usd=0.10)


def _usage(a1_in, a1_out, a2_in, a2_out, cost=None, calls=10):
    agents = {"agent1a": {"input": a1_in, "output": a1_out, "calls": 5},
              "agent2a": {"input": a2_in, "output": a2_out, "calls": 5}}
    total = {"input": a1_in + a2_in, "output": a1_out + a2_out, "calls": calls,
             "cost": cost or 0.0, "calls_without_cost": 0 if cost is not None else calls}
    return {"agents": agents, "total": total}


def test_totals_use_langsmith_cost_when_complete_and_table_otherwise():
    u = usage_totals(_usage(1_000_000, 100_000, 0, 0, cost=1.40))
    assert u["langsmith_cost"] == 1.40 and u["cost"] == 1.40
    assert u["table_cost"] == pytest.approx(1.0 + 0.5)          # $1/M in, $5/M out
    missing = usage_totals(_usage(1_000_000, 100_000, 0, 0, cost=None))
    assert missing["langsmith_cost"] is None and missing["cost"] == pytest.approx(1.5)


def test_ratio_test_needs_both_ratio_and_absolute_floor():
    assert usage_ratio_test("t", 1_000_000, [500_000] * 4, 0.5, 100_000)["status"] == "drift"
    assert usage_ratio_test("t", 700_000, [500_000] * 4, 0.5, 100_000)["status"] == "ok"        # +40%
    assert usage_ratio_test("t", 20_000, [10_000] * 4, 0.5, 100_000)["status"] == "ok"          # +100% but tiny
    assert usage_ratio_test("t", 5, [], 0.5, 100_000)["status"] == "insufficient_data"


def test_median_resists_one_odd_prior_week():
    r = usage_ratio_test("t", 520_000, [500_000, 510_000, 5_000_000, 490_000], 0.5, 100_000)
    assert r["status"] == "ok" and r["median"] == 505_000


def test_evaluate_flags_a_token_jump_and_names_the_agent():
    base = [usage_totals(_usage(400_000, 50_000, 100_000, 10_000, cost=0.5)) for _ in range(4)]
    cur = usage_totals(_usage(1_400_000, 50_000, 100_000, 10_000, cost=1.9))
    out = evaluate_usage(cur, base, **KW)
    flagged = {r["metric"] for r in out["results"] if r["status"] == "drift"}
    assert out["status"] == "drift" and {"total tokens", "total cost", "agent1a tokens"} <= flagged
    assert "agent2a tokens" not in flagged


def test_evaluate_steady_week_and_thin_history():
    base = [usage_totals(_usage(400_000, 50_000, 100_000, 10_000, cost=0.5)) for _ in range(4)]
    assert evaluate_usage(base[0], base, **KW)["status"] == "ok"
    assert evaluate_usage(base[0], base[:2], **KW) == {"status": "insufficient_history", "baseline_runs": 2, "results": []}
