"""agent4 click-tracking wiring: payload shape, fail-open preparation, and the send loop. Stubs only."""
import json

import pytest

import agent4_send as a4
import click_counts
import click_links
from fakes_firestore import FakeDb

RUN_DOC = {"news_summaries": {"Industry & Business": [{"url": "https://news.example/story", "title": "Story"}]},
           "paper_summaries": [{"title": "Paper", "pdf_url": "https://arxiv.org/pdf/1"}]}


# --- payload -----------------------------------------------------------------------------------

def test_default_payload_is_unchanged_by_click_tracking():
    p = a4.build_payload("r@example.com", "<b>hi</b>", "Subj")
    assert p == {"personalizations": [{"to": [{"email": "r@example.com"}]}],
                 "from": {"email": a4.SENDER_EMAIL, "name": a4.SENDER_NAME}, "subject": "Subj",
                 "content": [{"type": "text/html", "value": "<b>hi</b>"}]}


def test_tracked_payload_enables_click_tracking_and_tags_the_run_only():
    p = a4.build_payload("r@example.com", "<b>hi</b>", "Subj", click_run_id="RUN1")
    assert p["tracking_settings"] == {"click_tracking": {"enable": True, "enable_text": False}}
    assert p["personalizations"][0]["custom_args"] == {"run_id": "RUN1"}
    assert "r@example.com" not in json.dumps(p["personalizations"][0]["custom_args"])
    assert "open_tracking" not in p["tracking_settings"]


def test_send_email_posts_the_built_payload(monkeypatch):
    import urllib.request
    sent = {}

    class Resp:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req):
        sent["body"] = json.loads(req.data)
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    a4.send_email("k", "r@example.com", "<b>hi</b>", "Subj")
    assert "tracking_settings" not in sent["body"]
    a4.send_email("k", "r@example.com", "<b>hi</b>", "Subj", click_run_id="RUN1")
    assert sent["body"]["tracking_settings"]["click_tracking"]["enable"] is True


# --- preparation ---------------------------------------------------------------------------------

def test_prepare_stores_the_link_map_and_returns_the_run_id():
    db = FakeDb()
    assert a4._prepare_click_tracking(db, "RUN1", RUN_DOC) == "RUN1"
    doc = click_counts.load_link_doc(db, "RUN1")
    assert doc["sent_at"] > 0
    assert click_links.lookup(doc["links"], "https://news.example/story")[1]["category"] == "Industry & Business"
    assert click_links.lookup(doc["links"], "https://arxiv.org/pdf/1")[1]["category"] == "Papers"


def test_prepare_with_nothing_shipped_does_not_track():
    db = FakeDb()
    assert a4._prepare_click_tracking(db, "RUN1", {}) is None
    assert click_counts.load_link_doc(db, "RUN1") is None


def test_prepare_never_raises():
    class Boom:
        def collection(self, name):
            raise RuntimeError("firestore down")
    assert a4._prepare_click_tracking(Boom(), "RUN1", RUN_DOC) is None


# --- the send loop -------------------------------------------------------------------------------

@pytest.fixture
def loop(monkeypatch):
    db = FakeDb()
    sends = []
    import google.cloud.firestore as fs
    monkeypatch.setattr(fs, "Client", lambda project=None: db)
    monkeypatch.setattr(a4, "USE_FIRESTORE", True)
    monkeypatch.setattr(a4, "TEST_SEND_TO", "")
    monkeypatch.setattr(a4, "_load_latest_newsletter",
                        lambda d: a4.LoadedNewsletter({"0_0": "<p>{{UNSUBSCRIBE_URL}}</p>"}, "Subj", "RUN1", RUN_DOC))
    monkeypatch.setattr(a4, "_active_subscribers", lambda d: [{"email": "a@example.com", "token": "t1"},
                                                              {"email": "b@example.com", "token": "t2"}])
    monkeypatch.setattr(a4, "_load_user_section_configs", lambda d, s: {})
    monkeypatch.setattr(a4, "_get_sendgrid_api_key", lambda: "k")
    monkeypatch.setattr(a4, "send_email", lambda key, to, html, subj, click_run_id=None: sends.append((to, click_run_id)))
    return db, sends


def test_flag_off_sends_exactly_as_before(loop, monkeypatch):
    db, sends = loop
    monkeypatch.setattr(a4, "CLICK_TRACKING", False)
    a4.run("agent4-run")
    assert sends == [("a@example.com", None), ("b@example.com", None)]
    assert click_counts.load_link_doc(db, "RUN1") is None


def test_flag_on_tags_every_email_with_the_run_and_stores_the_map(loop, monkeypatch):
    db, sends = loop
    monkeypatch.setattr(a4, "CLICK_TRACKING", True)
    a4.run("agent4-run")
    assert sends == [("a@example.com", "RUN1"), ("b@example.com", "RUN1")]
    assert click_counts.load_link_doc(db, "RUN1")["links"]


def test_map_failure_still_sends_every_email_untracked(loop, monkeypatch):
    db, sends = loop
    monkeypatch.setattr(a4, "CLICK_TRACKING", True)
    monkeypatch.setattr(click_counts, "save_link_doc", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    a4.run("agent4-run")
    assert sends == [("a@example.com", None), ("b@example.com", None)]
