"""Cost estimation and the 2 USD approval guard required before paid eval runs."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents"))
from pricing import estimate_cost, register_price  # noqa: E402,F401  (price table lives in agents/pricing.py)

DEFAULT_LIMIT_USD = 2.0


class CostLimitExceeded(RuntimeError):
    pass


class CostGuard:
    """Refuses a run whose estimated cost exceeds the limit unless the user approved it."""

    def __init__(self, limit_usd=DEFAULT_LIMIT_USD, approved=False):
        self.limit_usd = limit_usd
        self.approved = approved

    def check(self, estimated_usd):
        if estimated_usd > self.limit_usd and not self.approved:
            raise CostLimitExceeded(
                f"estimated cost ${estimated_usd:.2f} exceeds ${self.limit_usd:.2f}; "
                "get the user's OK and rerun with approval"
            )
        return estimated_usd
