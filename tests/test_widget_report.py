"""The health check's informational Widgets section (agents/widget_report.py)."""
import widget_report
from fakes_firestore import FakeDb
from test_healthcheck_topics import _ago, _ai_doc, _check, _topic_doc


def test_format_section():
    text = widget_report.format_section([
        ("Latent SpaceMail", "r1", {"paper_spotlight": "ok"}),
        ("Game On", "r2", {"free_games": "ok", "most_played": "failed", "out_this_week": "skipped_no_key",
                           "deals": "timed_out", "number_of_week": "empty", "x": "weird"}),
    ])
    assert text.splitlines()[0] == widget_report.HEADER
    assert "  Latent SpaceMail (run r1): paper_spotlight ok" in text
    assert ("free_games ok, most_played FAILED (left out), out_this_week skipped: no key, deals timed out "
            "(left out), number_of_week nothing this week, x weird") in text
    assert widget_report.format_section([]) == ""


def test_healthcheck_without_summaries_has_no_section(monkeypatch):
    message, healthy = _check(monkeypatch, {"ai": _ai_doc(_ago(19))})
    assert healthy is True and "Widgets" not in message


def test_healthcheck_with_summaries_reports_but_never_flags(monkeypatch):
    ai = {**_ai_doc(_ago(19)), "widgets_summary": {"paper_spotlight": "ok"}}
    fin = _topic_doc(_ago(19), widgets_summary={"rates_board": "failed", "market_snapshot": "ok"})
    message, healthy = _check(monkeypatch, {"ai": ai, "fin": fin})
    assert healthy is True
    assert "Widgets (informational" in message
    assert "Latent SpaceMail (run ai): paper_spotlight ok" in message
    assert "(run fin): rates_board FAILED (left out), market_snapshot ok" in message
    assert message.index("Other newsletters") < message.index("Widgets (informational")


def test_debug_runs_are_not_reported():
    db = FakeDb({"pipeline_runs": {
        "d": {"started_at": _ago(1), "run_kind": "debug", "widgets_summary": {"paper_spotlight": "failed"}},
        "r": {"started_at": _ago(20), "run_kind": "release", "widgets_summary": {"paper_spotlight": "ok"}}}})
    text = widget_report.section(db, "pipeline_runs")
    assert "(run r): paper_spotlight ok" in text and "run d" not in text


def test_section_never_raises():
    class Broken:
        def collection(self, name):
            raise RuntimeError("firestore down")
    assert widget_report.section(Broken(), "pipeline_runs") == "\n\nWidgets: the report failed (RuntimeError)."
