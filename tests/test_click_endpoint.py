"""POST /sendgrid/events through FastAPI's TestClient: signature gate, bucketing, no subscriber data kept.

Firestore and the signing key are stubbed; nothing touches the network.
"""
import base64
import json
import logging
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs
import click_counts
import click_links
import sendgrid_webhook as sw

RUN = "2026-10-12T100004Z"
SENT_AT = 1_800_000_000
SHIPPED = "https://news.example/story"
PAPER = "https://arxiv.org/pdf/2601.0001"
PII = {"email": "reader@example.com", "ip": "203.0.113.7", "sg_message_id": "msgid.filter0001",
       "sg_event_id": "evt-abc123", "useragent": "Mozilla/5.0 (X11) Firefox/130"}


class Snap:
    def __init__(self, data):
        self._d, self.exists = data, data is not None

    def to_dict(self):
        return self._d


class CountDb:
    """Minimal Firestore: documents are dicts, set(merge=True) adds Increment values into nested maps."""

    def __init__(self, link_doc):
        self.docs = {("click_links", RUN): link_doc} if link_doc else {}
        self.link_reads = 0

    def collection(self, name):
        db = self
        return SimpleNamespace(document=lambda doc_id: _Ref(db, name, doc_id))


class _Ref:
    def __init__(self, db, name, doc_id):
        self.db, self.key = db, (name, doc_id)

    def get(self):
        if self.key[0] == "click_links":
            self.db.link_reads += 1
        return Snap(self.db.docs.get(self.key))

    def set(self, data, merge=False):
        doc = self.db.docs.setdefault(self.key, {})
        for bucket, counts in data.items():
            tgt = doc.setdefault(bucket, {})
            for k, inc in counts.items():
                tgt[k] = tgt.get(k, 0) + inc.value


@pytest.fixture(scope="module")
def keypair():
    private = ec.generate_private_key(ec.SECP256R1())
    der = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, base64.b64encode(der).decode()


@pytest.fixture
def api(monkeypatch, keypair):
    private, pub = keypair
    link_map = click_links.build_link_map({
        "news_summaries": {"Industry & Business": [{"url": SHIPPED, "title": "Story"}]},
        "paper_summaries": [{"title": "Paper", "pdf_url": PAPER}],
    })
    db = CountDb({"links": link_map, "sent_at": SENT_AT})
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    monkeypatch.setattr(subs.limiter, "enabled", False)
    monkeypatch.setattr(subs, "_get_sendgrid_webhook_public_key", lambda: pub)
    monkeypatch.setattr(subs, "_db", lambda: db)
    subs._LINK_DOC_CACHE.clear()
    return SimpleNamespace(client=TestClient(app), db=db, private=private, link_map=link_map)


def click(url=SHIPPED, offset=3600, **over):
    ev = {"event": "click", "url": url, "run_id": RUN, "timestamp": SENT_AT + offset, "category": ["newsletter"], **PII}
    ev.update(over)
    return ev


def post(api, events, ts=None, sign=True, body=None):
    ts = str(int(time.time())) if ts is None else str(ts)
    body = json.dumps(events).encode() if body is None else body
    headers = {"Content-Type": "application/json", sw.TIMESTAMP_HEADER: ts}
    if sign:
        sig = api.private.sign(ts.encode() + body, ec.ECDSA(hashes.SHA256()))
        headers[sw.SIGNATURE_HEADER] = base64.b64encode(sig).decode()
    return api.client.post("/sendgrid/events", content=body, headers=headers)


def counts(api):
    return api.db.docs.get(("click_counts", RUN), {})


def key_of(url):
    return click_links.url_key(url)


def test_counts_clicks_per_article_and_paper(api):
    r = post(api, [click(), click(), click(url=PAPER)])
    assert r.status_code == 200 and r.json() == {"received": 3, "counted": 3}
    assert counts(api) == {"clicks": {key_of(SHIPPED): 2, key_of(PAPER): 1}}


def test_early_bot_and_late_clicks_go_to_separate_buckets(api):
    post(api, [click(offset=60), click(offset=299), click(offset=300), click(offset=7200),
               click(useragent="Googlebot/2.1"), click(offset=-5)])
    c = counts(api)
    assert c["early"] == {key_of(SHIPPED): 3}
    assert c["clicks"] == {key_of(SHIPPED): 2}
    assert c["bots"] == {key_of(SHIPPED): 1}


def test_clicks_on_unshipped_links_and_unknown_runs_are_ignored(api):
    r = post(api, [click(url="https://example.com/unsubscribe?token=abc"), click(url="https://lofeodo.com/preferences"),
                   click(run_id="some-other-run"), click(event="open"), click(event="delivered")])
    assert r.status_code == 200 and r.json()["counted"] == 0
    assert counts(api) == {}


def test_bad_signature_is_403_and_body_is_never_parsed(api, monkeypatch):
    monkeypatch.setattr(sw, "reduce_events", lambda body: pytest.fail("parsed an unverified body"))
    body = json.dumps([click()]).encode()
    assert post(api, None, sign=False, body=body).status_code == 403
    other = ec.generate_private_key(ec.SECP256R1())
    ts = str(int(time.time()))
    sig = base64.b64encode(other.sign(ts.encode() + body, ec.ECDSA(hashes.SHA256()))).decode()
    r = api.client.post("/sendgrid/events", content=body,
                        headers={sw.TIMESTAMP_HEADER: ts, sw.SIGNATURE_HEADER: sig})
    assert r.status_code == 403 and counts(api) == {}


def test_tampered_body_and_stale_timestamp_are_403(api):
    ts = str(int(time.time()))
    body = json.dumps([click()]).encode()
    sig = base64.b64encode(api.private.sign(ts.encode() + body, ec.ECDSA(hashes.SHA256()))).decode()
    r = api.client.post("/sendgrid/events", content=body + b" ", headers={sw.TIMESTAMP_HEADER: ts, sw.SIGNATURE_HEADER: sig})
    assert r.status_code == 403
    assert post(api, [click()], ts=int(time.time()) - 3600).status_code == 403
    assert counts(api) == {}


def test_unconfigured_key_is_503(api, monkeypatch):
    def boom():
        raise RuntimeError("no key")
    monkeypatch.setattr(subs, "_get_sendgrid_webhook_public_key", boom)
    assert post(api, [click()]).status_code == 503 and counts(api) == {}


def test_non_array_body_is_400_after_a_valid_signature(api):
    assert post(api, None, body=b'{"event":"click"}').status_code == 400


def test_empty_batch_is_fine(api):
    r = post(api, [])
    assert r.status_code == 200 and r.json() == {"received": 0, "counted": 0}


def test_counting_failure_still_returns_200_and_logs_only_the_error_class(api, monkeypatch, caplog):
    def boom(db, tallies):
        raise RuntimeError("firestore down for reader@example.com")
    monkeypatch.setattr(click_counts, "apply_tallies", boom)
    with caplog.at_level(logging.DEBUG):
        r = post(api, [click()])
    assert r.status_code == 200 and r.json()["counted"] == 0
    assert "RuntimeError" in caplog.text and "reader@example.com" not in caplog.text


def test_no_subscriber_data_is_stored_or_logged(api, caplog):
    with caplog.at_level(logging.DEBUG):
        post(api, [click(), click(useragent="Googlebot/2.1"), click(event="delivered")])
        post(api, None, sign=False, body=json.dumps([click()]).encode())
    stored = json.dumps({str(k): v for k, v in api.db.docs.items()}, default=str)
    for secret in ("reader@example.com", "203.0.113.7", "msgid", "evt-abc123", "Firefox", "Googlebot"):
        assert secret not in stored and secret not in caplog.text


def test_link_doc_is_read_once_across_requests(api):
    post(api, [click()])
    post(api, [click(), click(url=PAPER)])
    assert api.db.link_reads == 1
    assert counts(api)["clicks"] == {key_of(SHIPPED): 2, key_of(PAPER): 1}


def test_prefetch_after_a_long_send_is_still_early():
    import click_counts as cc
    ev = {"timestamp": 10_000 + 60}
    # old docs (no window fields): measured from the send start
    assert cc.bucket_for(ev, 0) == "clicks"
    # send ran 0..10_000 s; a scanner hit a late recipient 60 s after the last email left
    assert cc.bucket_for(ev, 0, sent_end=10_000, send_complete=True) == "early"
    assert cc.bucket_for({"timestamp": 10_000 + 600}, 0, sent_end=10_000, send_complete=True) == "clicks"
    # send still running: inside the window counts as early, but only up to the cap
    assert cc.bucket_for({"timestamp": 5_000}, 0, send_complete=False) == "early"
    assert cc.bucket_for({"timestamp": cc.MAX_SEND_WINDOW_SECONDS + 600}, 0, send_complete=False) == "clicks"


def test_rerun_keeps_the_original_send_start():
    import click_counts as cc
    from fakes_firestore import FakeDb
    db = FakeDb()
    cc.save_link_doc(db, "r1", {"k": {}}, 100)
    cc.mark_send_complete(db, "r1", 500)
    cc.save_link_doc(db, "r1", {"k": {}}, 9_000)
    doc = cc.load_link_doc(db, "r1")
    assert doc["sent_at"] == 100 and doc["send_complete"] is False
