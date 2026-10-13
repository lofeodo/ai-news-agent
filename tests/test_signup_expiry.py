"""Never-confirmed sign-ups carry an expiry; confirming or signing in removes it; former subscribers never get one."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs
from google.cloud import firestore

NOW = datetime.now(timezone.utc)


class _Ref:
    def __init__(self, store, id):
        self.store, self.id = store, id

    def get(self):
        d = self.store.get(self.id)
        return SimpleNamespace(exists=d is not None, to_dict=lambda: None if d is None else dict(d), reference=self)

    def set(self, data, merge=False):
        self.store[self.id] = {**self.store.get(self.id, {}), **data} if merge else dict(data)

    def update(self, data):
        doc = self.store.setdefault(self.id, {})
        for k, v in data.items():
            if v is firestore.DELETE_FIELD:
                doc.pop(k, None)
            else:
                doc[k] = v


class _Db:
    def __init__(self, store):
        self.store = store

    def collection(self, name):
        db = self

        class _Q:
            def where(self, field, op, value):
                self.m = [i for i, d in db.store.items() if d.get(field) == value]
                return self

            def limit(self, n):
                return self

            def stream(self):
                return [_snap(db.store, i) for i in self.m]

        def _snap(store, i):
            r = _Ref(store, i).get()
            return SimpleNamespace(id=i, to_dict=r.to_dict, reference=r.reference)

        q = _Q()
        q.document = lambda id: _Ref(db.store, id)
        return q


@pytest.fixture
def api(monkeypatch):
    store, sent = {}, []
    monkeypatch.setattr(subs, "_db", lambda: _Db(store))
    monkeypatch.setattr(subs, "_send_email", lambda *a: sent.append(a[0]))
    monkeypatch.setattr(subs, "_active_subscriber_count", lambda _db: 0)
    monkeypatch.setattr(subs.mail_throttle, "allow_mail", lambda *a: True)
    monkeypatch.setattr(subs.mail_throttle, "allow_activation", lambda *a: True)
    monkeypatch.setattr(subs.limiter, "enabled", False)
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    return SimpleNamespace(client=TestClient(app), store=store)


BODY = {"email": "a@example.com", "prefs": {"include_french": False, "include_canada": False}}


def test_a_new_sign_up_expires_in_a_week_and_confirming_clears_it(api):
    api.client.post("/subscribe", json=BODY)
    doc = api.store["a@example.com"]
    assert timedelta(days=6) < doc["expires_at"] - NOW < timedelta(days=8)
    api.client.get("/confirm", params={"token": doc["token"]})
    assert "expires_at" not in api.store["a@example.com"]
    assert api.store["a@example.com"]["active"] is True


def test_a_former_subscriber_keeps_consent_record_and_gets_no_expiry(api):
    then = NOW - timedelta(days=90)
    api.store["a@example.com"] = {"email": "a@example.com", "active": False, "confirmed_at": then, "token": "old"}
    api.client.post("/subscribe", json=BODY)
    doc = api.store["a@example.com"]
    assert doc["confirmed_at"] == then and "expires_at" not in doc
