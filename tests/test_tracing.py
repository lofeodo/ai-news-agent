import os

import anthropic

import tracing


def _clear(monkeypatch):
    for v in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"):
        monkeypatch.delenv(v, raising=False)


def test_disabled_by_default_returns_plain_client(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_1ST_API_KEY", "sk-test")
    assert tracing.tracing_enabled() is False
    client = tracing.make_client()
    assert type(client) is anthropic.Anthropic
    tracing.flush()  # no-op, must not raise


def test_requires_both_flag_and_key(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert tracing.configure() is False
    assert os.environ["LANGSMITH_TRACING"] == "false"     # no key -> switched off, no auth errors
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert tracing.tracing_enabled() is True


def test_enabled_wraps_client_without_network(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_1ST_API_KEY", "sk-test")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test")
    plain_create = anthropic.Anthropic(api_key="x").messages.create
    client = tracing.make_client()
    assert isinstance(client, anthropic.Anthropic)
    assert client.messages.create is not plain_create and hasattr(client.messages.create, "__wrapped__")


def test_usage_tags():
    assert tracing.usage_tags("agent2a", "r1") == ["agent:agent2a", "run:r1"]
    assert tracing.usage_tags(None, None) == []
    assert tracing.usage_tags("agent3", None) == ["agent:agent3"]


def test_disabled_ignores_tags_and_passes_client_kwargs(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_1ST_API_KEY", "sk-test")
    client = tracing.make_client("agent1a", "r1", timeout=12.0)
    assert type(client) is anthropic.Anthropic and client.timeout == 12.0


def test_enabled_passes_tags_to_wrapper(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_1ST_API_KEY", "sk-test")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test")
    seen = {}
    import langsmith.wrappers as wrappers
    monkeypatch.setattr(wrappers, "wrap_anthropic", lambda c, tracing_extra=None: seen.update(extra=tracing_extra) or c)
    tracing.make_client("agent2b", "r9")
    assert seen["extra"] == {"tags": ["agent:agent2b", "run:r9"], "metadata": {"agent": "agent2b", "run_id": "r9"}}
