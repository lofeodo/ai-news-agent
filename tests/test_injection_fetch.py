"""The article fetcher must never reach internal addresses, even when a prompt-injected model asks."""
import socket

import pytest

import article_fetch as af


def _resolve(monkeypatch, mapping):
    def fake(host, port, *a, **k):
        if host not in mapping:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping[host], 0))]
    monkeypatch.setattr(af.socket, "getaddrinfo", fake)


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/", "http://localhost:8080/admin", "http://127.0.0.1/",
    "http://10.0.0.5/x", "http://192.168.1.1/", "http://172.16.0.9/", "http://[::1]/", "http://0.0.0.0/",
    "http://metadata.google.internal/computeMetadata/v1/", "http://service.local/", "http://LOCALHOST./x",
])
def test_internal_urls_are_blocked(monkeypatch, url):
    _resolve(monkeypatch, {"169.254.169.254": "169.254.169.254", "127.0.0.1": "127.0.0.1", "10.0.0.5": "10.0.0.5",
                           "192.168.1.1": "192.168.1.1", "172.16.0.9": "172.16.0.9", "::1": "::1",
                           "0.0.0.0": "0.0.0.0"})
    assert af.is_public_url(url) is False
    assert af.fetch_article_text_result(url) == (None, "blocked_url")


def test_hostname_resolving_to_private_address_is_blocked(monkeypatch):
    _resolve(monkeypatch, {"evil.example.com": "10.1.2.3"})
    assert af.fetch_article_text_result("https://evil.example.com/a") == (None, "blocked_url")


def test_public_and_unresolvable_hosts_are_allowed(monkeypatch):
    _resolve(monkeypatch, {"news.example.com": "93.184.216.34"})
    assert af.is_public_url("https://news.example.com/a") is True
    assert af.is_public_url("https://does-not-resolve.example/a") is True   # the fetch itself will fail


def test_blocked_url_never_reaches_the_downloader(monkeypatch):
    _resolve(monkeypatch, {"169.254.169.254": "169.254.169.254"})
    calls = []
    monkeypatch.setattr(af, "Article", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError))
    assert af.fetch_article_text_result("http://169.254.169.254/x")[1] == "blocked_url"
    assert calls == []
