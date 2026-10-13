"""Per-address pacing and daily ceilings for public mail routes. Firestore and mail are stubbed (no network)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs
import mail_throttle as mt

T0 = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


class _Ref:
    def __init__(self, store, name, id):
        self.store, self.name, self.id = store, name, id

    def get(self, transaction=None):
        data = self.store.get(self.name, {}).get(self.id)
        return SimpleNamespace(exists=data is not None, id=self.id, to_dict=lambda: None if data is None else dict(data))

    def set(self, data, merge=False):
        coll = self.store.setdefault(self.name, {})
        coll[self.id] = {**coll.get(self.id, {}), **data} if merge else dict(data)

    def update(self, data):
        self.store.setdefault(self.name, {}).setdefault(self.id, {}).update(data)


class _Txn:
    def set(self, ref, data):
        ref.set(data)


class _Db:
    def __init__(self, store):
        self.store = store

    def collection(self, name):
        return SimpleNamespace(document=lambda id: _Ref(self.store, name, id),
                               where=lambda *a: SimpleNamespace(count=lambda: SimpleNamespace(get=lambda: [[SimpleNamespace(value=0)]])))

    def transaction(self):
        return _Txn()


@pytest.fixture
def db(monkeypatch):
    import google.cloud.firestore as fs
    monkeypatch.setattr(fs, "transactional", lambda f: f)
    for var in ("MAIL_COOLDOWN_SECONDS", "MAIL_MAX_PER_ADDRESS_DAILY", "MAIL_DAILY_CEILING", "MAX_NEW_SUBSCRIBERS_PER_DAY"):
        monkeypatch.delenv(var, raising=False)
    return _Db({})


def test_a_second_mail_inside_the_cooldown_is_refused(db):
    assert mt.allow_mail(db, "a@example.com", T0)
    assert not mt.allow_mail(db, "a@example.com", T0 + timedelta(seconds=30))
    assert mt.allow_mail(db, "a@example.com", T0 + timedelta(seconds=61))


def test_the_fourth_mail_in_a_day_is_refused_and_the_next_day_resets(db):
    for i in range(3):
        assert mt.allow_mail(db, "a@example.com", T0 + timedelta(minutes=5 * i))
    assert not mt.allow_mail(db, "a@example.com", T0 + timedelta(minutes=20))
    assert mt.allow_mail(db, "a@example.com", T0 + timedelta(days=1))


def test_addresses_are_paced_independently_and_stored_hashed(db):
    assert mt.allow_mail(db, "a@example.com", T0)
    assert mt.allow_mail(db, "b@example.com", T0)
    assert "a@example.com" not in db.store[mt.THROTTLE_COLLECTION]
    assert mt.allow_mail(db, "  A@Example.com ", T0 + timedelta(seconds=1)) is False  # same address, other casing


def test_the_daily_mail_ceiling_stops_all_addresses(db, monkeypatch, capsys):
    monkeypatch.setenv("MAIL_DAILY_CEILING", "2")
    assert mt.allow_mail(db, "a@example.com", T0)
    assert mt.allow_mail(db, "b@example.com", T0)
    assert not mt.allow_mail(db, "c@example.com", T0)
    assert "ERROR mail skipped: daily_ceiling" in capsys.readouterr().out


def test_the_activation_ceiling_counts_per_day(db, monkeypatch):
    monkeypatch.setenv("MAX_NEW_SUBSCRIBERS_PER_DAY", "2")
    assert mt.allow_activation(db, T0) and mt.allow_activation(db, T0)
    assert not mt.allow_activation(db, T0)
    assert mt.allow_activation(db, T0 + timedelta(days=1))


def test_a_store_error_lets_the_request_through(db, monkeypatch):
    monkeypatch.setattr(db, "transaction", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert mt.allow_mail(db, "a@example.com", T0)
    assert mt.allow_activation(db, T0)


# --- the routes -----------------------------------------------------------------------------------------

@pytest.fixture
def api(db, monkeypatch):
    sent = []
    monkeypatch.setattr(subs, "_db", lambda: db)
    monkeypatch.setattr(subs, "_send_email", lambda to, subject, body: sent.append(to))
    monkeypatch.setattr(subs, "_active_subscriber_count", lambda _db: 0)
    monkeypatch.setattr(subs.limiter, "enabled", False)
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    return SimpleNamespace(client=TestClient(app), sent=sent, store=db.store)


def test_repeated_subscribe_sends_one_mail_and_answers_the_same(api):
    body = {"email": "a@example.com", "prefs": {"include_french": False, "include_canada": False}}
    first = api.client.post("/subscribe", json=body)
    token = api.store[subs.SUBSCRIBERS_COLLECTION]["a@example.com"]["token"]
    second = api.client.post("/subscribe", json=body)
    assert first.status_code == second.status_code == 200 and first.json() == second.json()
    assert api.sent == ["a@example.com"]
    assert api.store[subs.SUBSCRIBERS_COLLECTION]["a@example.com"]["token"] == token  # the refused call changed nothing


def test_request_links_are_paced_for_a_subscribed_address(api):
    api.store[subs.SUBSCRIBERS_COLLECTION] = {"a@example.com": {"email": "a@example.com", "active": True, "token": "t"}}
    for path in ("/request-unsubscribe", "/request-preferences", "/request-preferences"):
        assert api.client.post(path, json={"email": "a@example.com"}).json() == {"status": "ok"}
    assert api.sent == ["a@example.com"]  # one mail per address per minute across both routes


def test_unknown_address_gets_the_same_answer_and_no_mail(api):
    r = api.client.post("/request-preferences", json={"email": "nobody@example.com"})
    assert r.json() == {"status": "ok"} and api.sent == []
