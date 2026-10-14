"""The "send me last week's edition" mail uses the newest issue that was mailed, never a debug run or a pending draft."""
import agent_subscriptions as subs
from fakes_firestore import FakeDb

SENT = {"sent": 15, "total": 15}


def _run(started, html, **extra):
    return {"started_at": started, "newsletter_composed": True,
            "newsletter_variants": {"0_0": html}, **extra}


def _issue(runs):
    prefs = {"include_french": False, "include_canada": False}   # picks the 0_0 variant
    return subs._latest_newsletter_html(FakeDb({"pipeline_runs": runs}), "tok123", prefs)


def test_a_draft_waiting_for_monday_is_skipped():
    runs = {
        "last_week": _run("2026-10-05T16:00:00+00:00", "<p>last week {{UNSUBSCRIBE_URL}}</p>", agent4_send_summary=SENT),
        "draft":     _run("2026-10-12T16:00:00+00:00", "<p>draft</p>"),
    }
    html = _issue(runs)
    assert "last week" in html and "draft" not in html


def test_a_debug_run_is_never_sent():
    runs = {
        "last_week": _run("2026-10-05T16:00:00+00:00", "<p>last week</p>", agent4_send_summary=SENT),
        "debug":     _run("2026-10-13T09:00:00+00:00", "<p>debug</p>", run_kind="debug", agent4_send_summary=SENT),
    }
    assert "last week" in _issue(runs)


def test_the_footer_placeholder_is_replaced_with_the_subscribers_token():
    runs = {"a": _run("2026-10-05T16:00:00+00:00", "<a href='{{UNSUBSCRIBE_URL}}'>u</a>", agent4_send_summary=SENT)}
    html = _issue(runs)
    assert "token=tok123" in html and "{{" not in html


def test_nothing_mailed_yet_returns_none():
    assert _issue({"a": _run("2026-10-12T16:00:00+00:00", "<p>draft</p>")}) is None
