"""Cost estimation and the 2 USD approval guard required before paid eval runs."""
import config

DEFAULT_LIMIT_USD = 2.0

# USD per million tokens as (input, output). Keyed off config so no model name is hardcoded here;
# a later step that uses another model (e.g. a judge) registers its price with register_price().
# Haiku-class list price at time of writing; verify against current pricing before relying on it.
_PRICES = {config.SCORING_MODEL: (1.0, 5.0)}


def register_price(model, input_per_mtok, output_per_mtok):
    _PRICES[model] = (input_per_mtok, output_per_mtok)


def estimate_cost(input_tokens, output_tokens, model=None):
    """USD cost of a token count. `model` defaults to config.SCORING_MODEL."""
    model = model or config.SCORING_MODEL
    if model not in _PRICES:
        raise KeyError(f"no price registered for model {model!r}; call register_price()")
    in_price, out_price = _PRICES[model]
    return input_tokens / 1e6 * in_price + output_tokens / 1e6 * out_price


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
