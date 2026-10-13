"""Which address the rate limiter counts against. Requests are stubbed."""
from types import SimpleNamespace

import agent_subscriptions as subs


def _req(xff=None, peer="10.0.0.1"):
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=peer))


def test_cloud_run_uses_the_last_forwarded_entry(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "agent-subscriptions")
    assert subs._client_key(_req("6.6.6.6, 203.0.113.9")) == "203.0.113.9"


def test_a_client_supplied_entry_cannot_choose_the_key(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "agent-subscriptions")
    assert subs._client_key(_req("1.1.1.1", peer="10.0.0.1")) == "1.1.1.1"
    assert subs._client_key(_req("2.2.2.2, 203.0.113.9")) == subs._client_key(_req("3.3.3.3, 203.0.113.9"))


def test_missing_or_empty_header_falls_back_to_the_peer(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "agent-subscriptions")
    assert subs._client_key(_req()) == "10.0.0.1"
    assert subs._client_key(_req(" , ")) == "10.0.0.1"


def test_outside_cloud_run_the_header_is_ignored(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert subs._client_key(_req("203.0.113.9")) == "10.0.0.1"
