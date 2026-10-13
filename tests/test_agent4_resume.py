"""agent4 resumes a partly sent run: per-recipient sent markers, summary counts, test-send exemption."""
import pytest

import agent4_send as a4
from fakes_firestore import FakeDb

SUBS = [{"email": "a@example.com", "token": "t1"}, {"email": "b@example.com", "token": "t2"}]


@pytest.fixture
def env(monkeypatch):
    db = FakeDb()
    sends, fail = [], set()
    import google.cloud.firestore as fs
    monkeypatch.setattr(fs, "Client", lambda project=None: db)
    monkeypatch.setattr(a4, "USE_FIRESTORE", True)
    monkeypatch.setattr(a4, "TEST_SEND_TO", "")
    monkeypatch.setattr(a4, "CLICK_TRACKING", False)
    monkeypatch.setattr(a4, "_load_latest_newsletter",
                        lambda d: a4.LoadedNewsletter({"0_0": "<p>{{UNSUBSCRIBE_URL}}</p>"}, "Subj", "RUN1", {}))
    monkeypatch.setattr(a4, "_active_subscribers", lambda d: list(SUBS))
    monkeypatch.setattr(a4, "_load_user_section_configs", lambda d, s: {})
    monkeypatch.setattr(a4, "_get_sendgrid_api_key", lambda: "k")

    def fake_send(key, to, html, subj, click_run_id=None):
        if to in fail:
            raise RuntimeError("HTTP Error 500")
        sends.append(to)
    monkeypatch.setattr(a4, "send_email", fake_send)
    return db, sends, fail


def _summary(db):
    return db.store["pipeline_runs"]["RUN1"]["agent4_send_summary"]


def _marker_ids(db):
    return set(db.store.get("pipeline_runs/RUN1/sent", {}))


def test_a_first_run_marks_everyone_it_reached(env):
    db, sends, _ = env
    a4.run("agent4-run")
    assert sends == ["a@example.com", "b@example.com"]
    assert _marker_ids(db) == {a4._recipient_id(s["email"]) for s in SUBS}
    assert "a@example.com" not in "".join(db.store["pipeline_runs/RUN1/sent"])  # ids are hashes
    assert db.store["pipeline_runs/RUN1/sent"][a4._recipient_id("a@example.com")]["expires_at"]


def test_a_rerun_only_mails_recipients_not_yet_reached(env):
    db, sends, fail = env
    fail.add("b@example.com")
    a4.run("agent4-run")
    assert sends == ["a@example.com"] and _summary(db)["failed"] == 1
    fail.clear()
    sends.clear()
    a4.run("agent4-run")
    assert sends == ["b@example.com"]
    s = _summary(db)
    assert (s["total"], s["sent"], s["already_sent"], s["failed"]) == (2, 2, 1, 0)


def test_a_rerun_of_a_finished_send_mails_nobody_and_still_reports_sent(env):
    db, sends, _ = env
    a4.run("agent4-run")
    sends.clear()
    a4.run("agent4-run")
    assert sends == []
    s = _summary(db)
    assert s["sent"] == s["total"] == 2 and s["already_sent"] == 2  # the health check's "sent == 0" test stays quiet


def test_a_failed_send_leaves_no_marker(env):
    db, _, fail = env
    fail.add("b@example.com")
    a4.run("agent4-run")
    assert _marker_ids(db) == {a4._recipient_id("a@example.com")}


def test_test_sends_neither_read_nor_write_markers(env, monkeypatch):
    db, sends, _ = env
    db.store["pipeline_runs/RUN1/sent"] = {a4._recipient_id("me@example.com"): {}}
    monkeypatch.setattr(a4, "TEST_SEND_TO", "me@example.com")
    a4.run("agent4-run")
    assert sends == ["me@example.com"]  # a marker for the same address did not stop it
    assert set(db.store["pipeline_runs/RUN1/sent"]) == {a4._recipient_id("me@example.com")}  # and none was added


def test_a_marker_write_failure_does_not_raise():
    class Boom:
        def collection(self, name):
            raise RuntimeError("firestore down")
    a4._mark_sent(Boom(), "RUN1", "a@example.com")
