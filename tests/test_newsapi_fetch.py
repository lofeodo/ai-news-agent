"""NewsAPI calls: the key travels in a header and never appears in a URL or in the log line for a failed query."""
import requests

import agent1b_fetch_news as a1b

KEY = "SECRET-KEY-123"


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {"status": "ok", "articles": []}

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} Client Error for url: https://newsapi.org/v2/everything?apiKey={KEY}")
            err.response = self
            raise err

    def json(self):
        return self._body


def test_the_key_is_sent_in_a_header_not_the_url(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(params=params, headers=headers)
        return _Resp()

    monkeypatch.setattr(a1b.requests, "get", fake_get)
    a1b.fetch_newsapi_query("ai", "2026-10-01T00:00:00", KEY)
    assert "apiKey" not in seen["params"] and KEY not in str(seen["params"])
    assert seen["headers"] == {"X-Api-Key": KEY}


def test_a_failed_query_logs_no_key(monkeypatch, capsys):
    monkeypatch.setenv("NEWS_API_KEY", KEY)
    monkeypatch.setattr(a1b, "NEWSAPI_QUERIES", ["q1"])
    monkeypatch.setattr(a1b.requests, "get", lambda *a, **k: _Resp(status=429))
    assert a1b.fetch_newsapi_articles() == []
    out = capsys.readouterr().out
    assert KEY not in out and "HTTP 429" in out
