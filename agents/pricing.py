"""List prices for estimating Claude spend. Shared by the healthcheck's weekly cost section and
the paid-eval cost guard (evals/cost.py).

These are Anthropic list prices, not an invoice: batch discounts, data-residency multipliers and
negotiated rates are not modelled. Re-check the source page when PRICES_AS_OF gets old.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

PRICES_AS_OF = "2026-10-05"
PRICES_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"

# Cache multipliers on the base input price (5-minute cache write; cache hit).
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10

# USD per million tokens as (input, output). Keyed off config so no model name is hardcoded here;
# other models (e.g. a judge) register their price with register_price().
# Claude Haiku 4.5: $1 input / $5 output per MTok, verified against PRICES_SOURCE on PRICES_AS_OF.
_PRICES = {config.SCORING_MODEL: (1.0, 5.0)}


def register_price(model, input_per_mtok, output_per_mtok):
    _PRICES[model] = (input_per_mtok, output_per_mtok)


def estimate_cost(input_tokens, output_tokens, model=None, cache_read_tokens=0, cache_creation_tokens=0):
    """USD cost of a token count. `model` defaults to config.SCORING_MODEL.

    Anthropic reports cache tokens separately from `input_tokens`, so each is priced on its own.
    """
    model = model or config.SCORING_MODEL
    if model not in _PRICES:
        raise KeyError(f"no price registered for model {model!r}; call register_price()")
    in_price, out_price = _PRICES[model]
    return (
        input_tokens / 1e6 * in_price
        + output_tokens / 1e6 * out_price
        + cache_creation_tokens / 1e6 * in_price * CACHE_WRITE_MULT
        + cache_read_tokens / 1e6 * in_price * CACHE_READ_MULT
    )
