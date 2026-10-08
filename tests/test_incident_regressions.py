"""Regression tests for the fixes made after real incidents (docs/postmortems/). Stubs only.

2026-09-07 stale newsletter: parse_started_at, the healthcheck's stale check, agent4's freshness guard,
the watchdog deadline. 2026-09-07 / 2026-09-28 agent2b crash: isolated retry and the idempotent counter.
2026-09-11 dead footer link: the footer URL is built from FRONTEND_BASE_URL (not the deployed value).
"""
from datetime import datetime, timedelta, timezone

import signal

import pytest

import agent2b_summarize_news as a2b
import agent4_send as a4
import agent_healthcheck as hc
import config
import main
from fakes_firestore import FakeDb

NOW = datetime.now(timezone.utc)
ABORT = int(signal.SIGABRT)  # 6 on Linux (Cloud Run), differs on Windows


# --- parse_started_at (naive-datetime crash, healthcheck silent 2026-08-17 to 2026-09-07) -----------

def test_parse_started_at_treats_naive_as_utc():
    assert config.parse_started_at("2026-09-07T06:00:00") == datetime(2026, 9, 7, 6, tzinfo=timezone.utc)


def test_parse_started_at_keeps_aware_and_z_values():
    assert config.parse_started_at("2026-09-07T06:00:00+00:00") == datetime(2026, 9, 7, 6, tzinfo=timezone.utc)
    assert config.parse_started_at("2026-09-07T06:00:00Z") == datetime(2026, 9, 7, 6, tzinfo=timezone.utc)


def test_healthcheck_survives_a_naive_started_at_and_still_sends_its_report(monkeypatch):
    old_naive = (NOW - timedelta(hours=30)).replace(tzinfo=None).isoformat()
    db = FakeDb({"pipeline_runs": {"old": {"started_at": old_naive}}})
    sent = []
    import google.cloud.firestore as fs
    monkeypatch.setattr(fs, "Client", lambda project=None: db)
    monkeypatch.setattr(hc, "USE_FIRESTORE", True)
    monkeypatch.setattr(hc, "_notify", lambda message, healthy, mode="send": sent.append((message, healthy)))
    hc._run("t")
    assert len(sent) == 1 and sent[0][1] is False
    assert "No recent pipeline run" in sent[0][0]


# --- agent4 freshness guard (stale newsletter re-sent 2026-09-07) -----------------------------------

def _runs(started_at):
    return {"pipeline_runs": {"old": {"run_id": "old", "started_at": started_at, "newsletter_composed": True,
                                      "newsletter_variants": {"0_0": "<p>x</p>"}, "newsletter_subject": "S"}}}


def test_agent4_refuses_a_newsletter_older_than_the_limit():
    old = (NOW - timedelta(hours=a4.MAX_NEWSLETTER_AGE_HOURS + 1)).isoformat()
    with pytest.raises(a4.StaleNewsletterError) as exc:
        a4._load_latest_newsletter(FakeDb(_runs(old)))
    assert exc.value.run_id == "old"  # the error is recorded against the stale run's own doc
    assert exc.value.age_hours > a4.MAX_NEWSLETTER_AGE_HOURS


def test_agent4_refuses_a_week_old_run_with_a_naive_timestamp():
    old = (NOW - timedelta(days=14)).replace(tzinfo=None).isoformat()
    with pytest.raises(a4.StaleNewsletterError):
        a4._load_latest_newsletter(FakeDb(_runs(old)))


def test_agent4_loads_a_fresh_newsletter():
    loaded = a4._load_latest_newsletter(FakeDb(_runs((NOW - timedelta(hours=2)).isoformat())))
    assert loaded.run_id == "old" and set(loaded.variants) == {"0_0"} and loaded.subject == "S"


# --- footer links (FRONTEND_BASE_URL dead preferences link) -----------------------------------------

def test_footer_links_are_built_from_the_configured_base_urls(monkeypatch):
    monkeypatch.setattr(a4, "FRONTEND_BASE_URL", "https://front.example")
    monkeypatch.setattr(a4, "SERVICE_BASE_URL", "https://svc.example")
    html = a4._personalize("{{PREFERENCES_URL}} {{UNSUBSCRIBE_URL}}", "TOK")
    assert html == "https://front.example/preferences.html?token=TOK https://svc.example/unsubscribe?token=TOK"
    assert "{{" not in html


# --- watchdog deadline (hard runtime cap) -----------------------------------------------------------

def _freeze(monkeypatch, local_iso):
    """Make main._deadline_seconds see 'now' as the given America/Toronto wall-clock time."""
    fixed = datetime.fromisoformat(local_iso).replace(tzinfo=main._CUTOFF_TZ).astimezone(timezone.utc)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(main, "datetime", _DT)


def test_deadline_is_the_flat_cap_early_in_the_morning(monkeypatch):
    _freeze(monkeypatch, "2026-10-12T06:00:00")  # cutoff 07:30 is 90 min away, cap is 60 min
    assert main._deadline_seconds() == main.MAX_RUNTIME_SECONDS


def test_deadline_is_the_0730_cutoff_when_that_comes_first(monkeypatch):
    _freeze(monkeypatch, "2026-10-12T07:00:00")
    assert main._deadline_seconds() == pytest.approx(30 * 60, abs=1)


def test_run_started_after_the_cutoff_gets_only_the_flat_cap(monkeypatch):
    _freeze(monkeypatch, "2026-10-12T14:00:00")
    assert main._deadline_seconds() == main.MAX_RUNTIME_SECONDS


# --- crash-isolated retry (agent2b native aborts) ---------------------------------------------------

class _Proc:
    def __init__(self, codes):
        self._codes = codes

    def wait(self):
        return self._codes.pop(0)


def _isolated(monkeypatch, codes):
    import subprocess
    writes, seq = [], list(codes)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _Proc(seq))
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(main, "_update_run_doc", lambda run_id, fn: writes.append((run_id, fn)))
    main._run_isolated("agent2b", "agent2b_summarize_news", "R1", 5, {"proc": None})
    return writes, seq


class _FS:
    DELETE_FIELD = "DELETED"


def test_isolated_agent_recovers_after_a_native_abort_and_clears_the_stale_error(monkeypatch):
    writes, seq = _isolated(monkeypatch, [-ABORT, -ABORT, 0])  # SIGABRT twice, then success
    assert seq == []  # stopped at the first success
    assert len(writes) == 1
    assert writes[0][1](_FS) == {"agent2b_error": "DELETED", "agent2b_failed_at": "DELETED", "agent2b_attempts": 3}


def test_isolated_agent_records_a_crash_when_every_attempt_dies_by_signal(monkeypatch):
    recorded = []
    monkeypatch.setattr(main, "_record_failure", lambda agent, run_id, error: recorded.append((agent, run_id, error)))
    _isolated(monkeypatch, [-ABORT] * 6)
    assert recorded == [("agent2b", "R1", "crashed (killed by SIGABRT) on all 6 attempts")]


def test_isolated_agent_first_try_success_writes_nothing(monkeypatch):
    writes, _ = _isolated(monkeypatch, [0])
    assert writes == []


# --- idempotent agent2 counter ----------------------------------------------------------------------

class _Ref:
    def __init__(self, data):
        self.data = data

    def get(self, transaction=None):
        return type("S", (), {"to_dict": lambda s: dict(self.data)})()


class _Txn:
    def update(self, ref, fields):
        ref.data.update(fields)


def _counter(monkeypatch, data):
    import google.cloud.firestore as fs
    ref = _Ref(data)
    db = type("D", (), {"collection": lambda s, n: type("C", (), {"document": lambda s2, i: ref})(),
                        "transaction": lambda s: _Txn()})()
    monkeypatch.setattr(fs, "Client", lambda project=None: db)
    monkeypatch.setattr(fs, "transactional", lambda f: f)
    return ref


def test_agent2b_counter_increments_once_even_if_the_agent_is_retried(monkeypatch):
    ref = _counter(monkeypatch, {})
    assert a2b.increment_and_check("R1") is False  # agent2a has not finished: 1 of 2
    assert a2b.increment_and_check("R1") is False  # retried attempt must not push the count to 2
    assert ref.data["agent2_completions"] == 1 and ref.data["agent2b_counted"] is True


def test_agent2b_counter_reaches_two_when_agent2a_already_counted(monkeypatch):
    _counter(monkeypatch, {"agent2_completions": 1})
    assert a2b.increment_and_check("R1") is True


# --- run doc size (Firestore 1 MiB cap, 2026-09-28) -------------------------------------------------

def test_agent3_run_doc_update_stays_under_the_firestore_cap_and_keeps_health_check_fields():
    import json
    import agent3_compose as a3

    cats = a3.NEWS_CATEGORIES
    # ~500 articles with ~1.7 KB each (the size the 2026-09-28 run had), and four ~105 KB variants.
    by_category = {c: [{"url": f"https://n.example/{c}/{i}", "title": "t" * 100, "summary": "s" * 1600}
                       for i in range(500 // len(cats) + 1)] for c in cats}
    selected_all = {c: by_category[c][:4] for c in cats}
    selected_en = {c: by_category[c][2:6] for c in cats}  # overlaps selected_all by 2 per category
    variants = {k: "<p>" + "x" * 105_000 + "</p>" for k in ("0_0", "1_0", "0_1", "1_1")}

    unpruned = {"news_summaries": by_category, "news_filtered": by_category, "newsletter_variants": variants}
    assert len(json.dumps(unpruned)) > 1_048_576  # the test data really reproduces the failure

    update = a3.build_run_doc_update(variants, "Subject", selected_all, selected_en, by_category)
    assert len(json.dumps(update)) < 1_048_576
    assert update["newsletter_composed"] is True and update["newsletter_html"] == variants["0_0"]
    assert update["news_summaries"] and update["news_filtered"]  # the health check tests these for presence
    for arts in update["news_summaries"].values():  # shipped only, no duplicate across the two passes
        urls = [a["url"] for a in arts]
        assert len(urls) == len(set(urls)) == 6
    assert update["news_filtered"]["article_counts"] == {c: len(a) for c, a in by_category.items()}


# --- release vs debug runs: agent4 never mails a debug run -----------------------------------------

def _two_runs(newest_kind):
    runs = _runs((NOW - timedelta(hours=3)).isoformat())["pipeline_runs"]
    runs["old"]["run_id"] = "release_run"
    runs["debug_run"] = {"run_id": "debug_run", "started_at": (NOW - timedelta(hours=1)).isoformat(),
                         "run_kind": newest_kind, "newsletter_composed": True,
                         "newsletter_variants": {"0_0": "<p>d</p>"}}
    return {"pipeline_runs": runs}


def test_agent4_skips_a_newer_debug_run(monkeypatch):
    monkeypatch.setattr(a4, "TEST_SEND_TO", "")
    assert a4._load_latest_newsletter(FakeDb(_two_runs("debug"))).run_id == "old"


def test_agent4_test_send_may_use_a_debug_run(monkeypatch):
    monkeypatch.setattr(a4, "TEST_SEND_TO", "me@example.com")
    assert a4._load_latest_newsletter(FakeDb(_two_runs("debug"))).run_id == "debug_run"


def test_healthcheck_checks_the_latest_release_run_not_a_newer_debug_run():
    db = FakeDb({"pipeline_runs": {
        "rel": {"started_at": (NOW - timedelta(hours=5)).isoformat()},
        "dbg": {"started_at": (NOW - timedelta(hours=1)).isoformat(), "run_kind": "debug"},
    }})
    assert hc._latest_run_doc(db)[0] == "rel"
