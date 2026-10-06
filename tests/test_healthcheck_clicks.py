"""Healthcheck click section: informational, never changes status, never suppresses the heartbeat."""
import agent_healthcheck as hc
import click_counts
import click_links as cl
from test_healthcheck_drift import _runs, _setup

URL = "https://news.example/story"
KEY = cl.url_key(URL)


def _track(db, run_id, clicks=0, early=0, bots=0):
    db.store.setdefault("click_links", {})[run_id] = {
        "links": {KEY: {"url": URL, "title": "The Story", "category": "Industry & Business"}}, "sent_at": 1}
    db.store.setdefault("click_counts", {})[run_id] = {
        "clicks": {KEY: clicks}, "early": {KEY: early}, "bots": {KEY: bots}}


def test_untracked_run_says_so_and_stays_healthy(monkeypatch):
    _, sent = _setup(monkeypatch, _runs(4))
    hc._run("t")
    (message, healthy), = sent
    assert healthy is True and "Reader clicks" in message and "click tracking was not on for this run" in message


def test_report_shows_prior_week_clicks_and_this_weeks_partial(monkeypatch):
    runs = _runs(4)
    runs["p3"]["agent4_send_summary"] = {"sent": 20, "total": 20, "failed": 0}
    db, sent = _setup(monkeypatch, runs)
    _track(db, "cur", clicks=1)
    _track(db, "p3", clicks=5, early=9, bots=2)
    hc._run("t")
    (message, healthy), = sent
    assert healthy is True
    assert "1 click so far" in message
    assert "last tracked week (run p3): 5 clicks (0.25 per delivered email, 20 delivered)" in message
    assert "9 in the first minutes" in message and '"The Story" (5, Industry & Business)' in message


def test_no_subscriber_data_reaches_the_email(monkeypatch):
    runs = _runs(4)
    db, sent = _setup(monkeypatch, runs)
    _track(db, "p3", clicks=3)
    hc._run("t")
    assert "@" not in sent[0][0].split("Reader clicks")[1]


def test_click_failure_does_not_suppress_heartbeat_or_change_status(monkeypatch):
    _, sent = _setup(monkeypatch, _runs(4))
    monkeypatch.setattr(click_counts, "load_link_doc", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    hc._run("t")
    (message, healthy), = sent
    assert healthy is True and "Reader clicks: failed" in message and "boom" in message
