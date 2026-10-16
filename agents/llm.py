# agents/llm.py
#
# The one place that decides which model answers which stage. Every stage asks `model_for("<stage>")`
# for its model id and builds its client with `make_client(...)`.
#
# Default (no MODEL_* variable set): model_for() returns config.SCORING_MODEL and make_client() returns
# exactly what tracing.make_client() returns, so behavior is unchanged. Setting MODEL_<STAGE> to a DeepSeek
# model (e.g. MODEL_LANG_FILTER=deepseek-flash) routes only that stage's calls to DeepSeek's
# Anthropic-compatible endpoint; everything else stays on Claude. Rollback is unsetting the variable.
#
# DeepSeek specifics, tested 2026-10-16:
#   - It thinks by default (extra billed output tokens) and then rejects a forced tool_choice, so the seam
#     sends thinking={"type": "disabled"}.
#   - A DeepSeek failure other than a rate limit falls back to Claude for that one call, so a provider
#     outage never fails a run. Rate limits propagate so each caller's own retry loop handles them.
#   - Only public article text goes to DeepSeek; subscriber data never reaches this module.

import os
import sys
import threading

import anthropic

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import tracing  # noqa: E402

STAGES = (
    "lang_filter",    # agent1b language filter
    "categorize",     # agent1b categorize
    "paper_score",    # agent1a paper scoring
    "news_summary",   # agent2b article summaries
    "paper_summary",  # agent2a paper summary
    "select",         # agent3 selection and intro
)

DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"

_stats_lock = threading.Lock()
fallbacks = 0  # DeepSeek calls that fell back to Claude this process (logged, not persisted)


def is_deepseek(model) -> bool:
    return isinstance(model, str) and model.startswith("deepseek")


def model_for(stage: str) -> str:
    """Model id for a stage: MODEL_<STAGE> if set, else the Claude default."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    return (os.environ.get(f"MODEL_{stage.upper()}") or "").strip() or config.SCORING_MODEL


def routing_active() -> bool:
    return any(is_deepseek(model_for(s)) for s in STAGES)


def _deepseek_client(agent, run_id, **client_kwargs):
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    client = anthropic.Anthropic(api_key=key, base_url=DEEPSEEK_BASE_URL, **client_kwargs)
    return tracing.wrap_for_tracing(client, agent, run_id)


class _Messages:
    def __init__(self, owner):
        self._o = owner

    def create(self, **kwargs):
        model = kwargs.get("model")
        if not is_deepseek(model) or self._o.deepseek is None:
            if is_deepseek(model):  # key missing: never fail a run over the optional provider
                kwargs = {**kwargs, "model": config.SCORING_MODEL}
                self._o._note_fallback("DEEPSEEK_API_KEY is not set")
            return self._o.claude.messages.create(**kwargs)
        try:
            return self._o.deepseek.messages.create(**{**kwargs, "thinking": {"type": "disabled"}})
        except anthropic.RateLimitError:
            raise
        except anthropic.APIError as e:
            self._o._note_fallback(f"{type(e).__name__}: {str(e)[:120]}")
            return self._o.claude.messages.create(**{**kwargs, "model": config.SCORING_MODEL})


class RoutedClient:
    """Looks like an Anthropic client; sends DeepSeek model ids to DeepSeek and the rest to Claude."""

    def __init__(self, claude, deepseek):
        self.claude, self.deepseek = claude, deepseek
        self.messages = _Messages(self)

    def _note_fallback(self, why):
        global fallbacks
        with _stats_lock:
            fallbacks += 1
        print(f"  [llm] DeepSeek call fell back to Claude: {why}")


def make_client(agent=None, run_id=None, **client_kwargs):
    """Claude client from tracing.make_client, or a RoutedClient when any stage is set to DeepSeek."""
    claude = tracing.make_client(agent, run_id, **client_kwargs)
    if not routing_active():
        return claude
    return RoutedClient(claude, _deepseek_client(agent, run_id, **client_kwargs))
