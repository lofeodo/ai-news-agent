"""GET /archive and GET /archive/{run_id}: public, explicit-release only. Firestore is stubbed."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs
import run_kind

DOCS = {
    "2026-09-21T100003Z": {"run_kind": "release", "newsletter_composed": True,
                           "newsletter_variants": {"0_1": '<a href="https://example.com/x">story</a>'}},
    "2026-06-22T100004Z": {"newsletter_composed": True, "newsletter_variants": {"0_1": "<p>legacy</p>"}},
    "2026-10-08T124639Z": {"run_kind": "debug", "newsletter_composed": True, "newsletter_variants": {"0_1": "<p>dbg</p>"}},
    "2026-10-09T000000Z": {"run_kind": "release", "newsletter_variants": {"0_1": "<p>uncomposed</p>"}},
}


class _Snap:
    def __init__(self, doc_id, data):
        self.id, self._d, self.exists = doc_id, data, data is not None

    def to_dict(self):
        return self._d


@pytest.fixture
def client(monkeypatch):
    db = SimpleNamespace(collection=lambda name: SimpleNamespace(
        document=lambda i: SimpleNamespace(get=lambda: _Snap(i, DOCS.get(i)))))
    monkeypatch.setattr(subs, "_db", lambda: db)
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    return TestClient(app)


def test_list_returns_only_flagged_composed_releases(client, monkeypatch):
    rows = [
        _Snap("a", {"run_kind": "release", "newsletter_composed": True, "newsletter_subject": "S1",
                    "started_at": "2026-09-21T10:00:00"}),
        _Snap("b", {"newsletter_composed": True}),                       # legacy, unflagged
        _Snap("c", {"run_kind": "debug", "newsletter_composed": True}),
    ]
    scan = SimpleNamespace(where=lambda *a, **k: scan, order_by=lambda *a, **k: scan, select=lambda *a, **k: scan,
                           limit=lambda *a, **k: scan, stream=lambda: iter(rows))
    monkeypatch.setattr(subs, "_db", lambda: SimpleNamespace(collection=lambda n: scan))
    body = client.get("/archive").json()
    assert body == {"issues": [{"run_id": "a", "subject": "S1", "started_at": "2026-09-21T10:00:00"}]}


def test_issue_html_is_rendered_and_fenced(client):
    r = client.get("/archive/2026-09-21T100003Z")
    assert r.status_code == 200 and 'target="_blank"' in r.text
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]


@pytest.mark.parametrize("run_id", [
    "2026-10-08T124639Z",   # debug
    "2026-06-22T100004Z",   # legacy, never flagged
    "2026-10-09T000000Z",   # flagged but not composed
    "does-not-exist",
    "__bad__",
])
def test_issue_404_for_anything_not_an_archived_release(client, run_id):
    r = client.get(f"/archive/{run_id}")
    assert r.status_code == 404 and r.headers["Content-Security-Policy"] == subs.PREVIEW_CSP


def test_is_archived_release_needs_explicit_flag():
    assert run_kind.is_archived_release({"run_kind": "release", "newsletter_composed": True})
    assert not run_kind.is_archived_release({"newsletter_composed": True})
    assert not run_kind.is_archived_release(None)
