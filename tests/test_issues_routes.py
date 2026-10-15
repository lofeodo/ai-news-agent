"""The issue routes (/issues, /issues/latest, /issues/{run_id}) and the old names that still answer."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs
import run_kind


class _Snap:
    def __init__(self, doc_id, data):
        self.id, self._d, self.exists = doc_id, data, data is not None

    def to_dict(self):
        return self._d


@pytest.fixture
def client(monkeypatch):
    docs = {"r1": {"run_kind": "release", "newsletter_composed": True, "newsletter_variants": {"0_1": "<p>one</p>"}}}
    monkeypatch.setattr(subs, "_db", lambda: SimpleNamespace(collection=lambda n: SimpleNamespace(
        document=lambda i: SimpleNamespace(get=lambda: _Snap(i, docs.get(i))))))
    monkeypatch.setattr(run_kind, "latest_run", lambda *a, **k: _Snap("r1", docs["r1"]))
    monkeypatch.setattr(run_kind, "list_releases", lambda *a, **k: [{"run_id": "r1", "subject": "S", "started_at": ""}])
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    return TestClient(app)


@pytest.mark.parametrize("path", ["/issues/latest", "/preview", "/issues/r1", "/archive/r1"])
def test_html_routes_serve_the_issue_with_the_embed_policy(client, path):
    r = client.get(path)
    assert r.status_code == 200 and "one" in r.text
    assert r.headers["Content-Security-Policy"] == subs.PREVIEW_CSP


@pytest.mark.parametrize("path", ["/issues", "/archive"])
def test_list_routes_match(client, path):
    assert client.get(path).json() == {"issues": [{"run_id": "r1", "subject": "S", "started_at": ""}]}


def test_latest_is_not_treated_as_a_run_id(client):
    # /issues/latest must hit the latest-release handler, not look up a run called "latest"
    assert client.get("/issues/latest").status_code == 200


@pytest.mark.parametrize("path", ["/auth/admin/issues", "/auth/admin/preview"])
def test_admin_routes_require_login(client, path):
    assert client.get(path).status_code == 401
