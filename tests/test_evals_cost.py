import pytest

import config
from evals.cost import CostGuard, CostLimitExceeded, estimate_cost, register_price


def test_estimate_cost_default_model():
    assert estimate_cost(1_000_000, 1_000_000) == pytest.approx(6.0)
    assert estimate_cost(0, 0) == 0.0


def test_estimate_cost_unknown_model_raises():
    with pytest.raises(KeyError):
        estimate_cost(10, 10, model="not-registered")


def test_register_price():
    register_price("test-model", 2.0, 10.0)
    assert estimate_cost(1_000_000, 0, model="test-model") == pytest.approx(2.0)
    assert estimate_cost(1_000_000, 0, model=config.SCORING_MODEL) == pytest.approx(1.0)


def test_guard_blocks_over_limit_without_approval():
    guard = CostGuard()
    assert guard.check(1.99) == 1.99
    with pytest.raises(CostLimitExceeded):
        guard.check(2.01)


def test_guard_passes_with_approval():
    assert CostGuard(approved=True).check(50.0) == 50.0


def test_cache_tokens_are_priced_separately():
    # 1M cache-read tokens at 10% of the $1 input price; 1M cache-write tokens at 125%.
    assert estimate_cost(0, 0, cache_read_tokens=1_000_000) == pytest.approx(0.10)
    assert estimate_cost(0, 0, cache_creation_tokens=1_000_000) == pytest.approx(1.25)
