"""agents/llm.py: stage -> model routing, DeepSeek call shaping and the fall back to Claude. No network."""
import anthropic
import httpx
import pytest

import config
import llm
import pricing


class FakeClient:
    def __init__(self, result=None, error=None):
        self.calls, self._result, self._error = [], result or object(), error
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self._error:
            raise self._error
        return self._result


def _api_error(cls, status=500):
    req = httpx.Request("POST", "https://x")
    resp = httpx.Response(status, request=req)
    return cls("boom", response=resp, body=None)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for s in llm.STAGES:
        monkeypatch.delenv(f"MODEL_{s.upper()}", raising=False)
    monkeypatch.setattr(llm, "fallbacks", 0)


def test_default_is_claude_for_every_stage():
    assert {llm.model_for(s) for s in llm.STAGES} == {config.SCORING_MODEL}
    assert not llm.routing_active()


def test_stage_override_and_unknown_stage(monkeypatch):
    monkeypatch.setenv("MODEL_LANG_FILTER", "deepseek-flash")
    assert llm.model_for("lang_filter") == "deepseek-flash"
    assert llm.model_for("categorize") == config.SCORING_MODEL
    assert llm.routing_active()
    with pytest.raises(ValueError):
        llm.model_for("nope")


def test_make_client_without_routing_is_the_plain_client(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(llm.tracing, "make_client", lambda *a, **k: sentinel)
    assert llm.make_client("agent1b", "r1") is sentinel


def test_claude_model_goes_to_claude_untouched():
    claude, ds = FakeClient(), FakeClient()
    llm.RoutedClient(claude, ds).messages.create(model=config.SCORING_MODEL, max_tokens=5)
    assert claude.calls == [{"model": config.SCORING_MODEL, "max_tokens": 5}]
    assert ds.calls == []


def test_deepseek_model_goes_to_deepseek_with_thinking_off():
    claude, ds = FakeClient(), FakeClient()
    llm.RoutedClient(claude, ds).messages.create(model="deepseek-flash", max_tokens=5)
    assert ds.calls == [{"model": "deepseek-flash", "max_tokens": 5, "thinking": {"type": "disabled"}}]
    assert claude.calls == []


def test_deepseek_error_falls_back_to_claude_haiku():
    claude, ds = FakeClient(), FakeClient(error=_api_error(anthropic.InternalServerError))
    llm.RoutedClient(claude, ds).messages.create(model="deepseek-flash", max_tokens=5)
    assert claude.calls == [{"model": config.SCORING_MODEL, "max_tokens": 5}]
    assert llm.fallbacks == 1


def test_rate_limit_propagates_so_the_callers_retry_loop_handles_it():
    claude, ds = FakeClient(), FakeClient(error=_api_error(anthropic.RateLimitError, 429))
    with pytest.raises(anthropic.RateLimitError):
        llm.RoutedClient(claude, ds).messages.create(model="deepseek-flash", max_tokens=5)
    assert claude.calls == []


def test_missing_deepseek_key_falls_back_instead_of_failing():
    claude = FakeClient()
    llm.RoutedClient(claude, None).messages.create(model="deepseek-flash", max_tokens=5)
    assert claude.calls == [{"model": config.SCORING_MODEL, "max_tokens": 5}]


def test_prices_registered():
    assert pricing.estimate_cost(1_000_000, 1_000_000, "deepseek-flash") == pytest.approx(0.75)
    assert pricing.estimate_cost(1_000_000, 1_000_000, "deepseek-flash@peak") == pytest.approx(1.5)
