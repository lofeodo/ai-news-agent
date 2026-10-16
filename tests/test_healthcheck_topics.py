"""The health check covers every newsletter that is running: one email, a section per other newsletter."""
from datetime import datetime, timedelta, timezone

import agent_healthcheck as hc
from fakes_firestore import FakeDb

NOW = datetime.now(timezone.utc)


def _ago(hours):
    return (NOW - timedelta(hours=hours)).isoformat()


def _ai_doc(started):
    doc = {"started_at": started, "run_kind": "release"}
    for field, _ in hc.EXPECTED_STAGES:
        doc[field] = {"ok": True}
    return doc


def _topic_html():
    return "<html>" + "x" * 6000 + "Read&nbsp;&#8594;" * 6 + "{{UNSUBSCRIBE_URL}} {{PREFERENCES_URL}}</html>"


def _topic_doc(started, topic="finance", kind="release", **extra):
    """A healthy run of a topic without a paper spotlight: its paper stages are empty lists."""
    doc = {"started_at": started, "run_kind": kind, "topic": topic,
           "scored_papers": [], "paper_candidates": [], "agent1a_skipped": "no spotlight for topic",
           "paper_summaries": [], "agent2a_skipped": "no spotlight for topic",
           "news_filtered": {"by_category": {"A": 9}}, "news_summaries": {"A": [1]}, "newsletter_composed": True,
           "newsletter_variants": {"0_0": _topic_html()},
           "newsletter_subject": "Markets & Money Weekly — " + _send_day(started),
           "agent4_send_summary": {"total": 3, "sent": 3, "failed": 0}, **extra}
    return doc


def _send_day(started):
    import config
    return config.newsletter_send_date(config.parse_started_at(started)).strftime("%B %d, %Y")


def _check(monkeypatch, runs, mode="send"):
    db = FakeDb({"pipeline_runs": runs})
    sent = []
    import google.cloud.firestore as fs
    monkeypatch.setattr(fs, "Client", lambda project=None: db)
    monkeypatch.setattr(hc, "USE_FIRESTORE", True)
    monkeypatch.setattr(hc, "_notify", lambda message, healthy, mode="send": sent.append((message, healthy)))
    hc._run("t", mode)
    (message, healthy), = sent
    return message, healthy


def test_ai_only_world_is_unchanged(monkeypatch):
    message, healthy = _check(monkeypatch, {"ai": _ai_doc(_ago(19))})
    assert healthy is True and "Other newsletters" not in message
    assert message.startswith("Pipeline run ai (started ")


def test_topic_debug_runs_do_not_make_a_topic_expected(monkeypatch):
    message, healthy = _check(monkeypatch, {"ai": _ai_doc(_ago(19)),
                                            "fin-dbg": _topic_doc(_ago(2), kind="debug", agent3_error="boom")})
    assert healthy is True and "Other newsletters" not in message


def test_a_healthy_topic_is_listed_and_the_email_stays_all_clear(monkeypatch):
    message, healthy = _check(monkeypatch, {"ai": _ai_doc(_ago(19)), "fin": _topic_doc(_ago(21))})
    assert healthy is True
    assert "Other newsletters:\nMarkets & Money Weekly (finance): run fin" in message and "completed successfully" in message
    assert message.index("Pipeline run ai") < message.index("Other newsletters")      # the AI report reads as today


def test_a_healthy_topic_draft_passes_its_own_composition_check(monkeypatch):
    doc = _topic_doc(_ago(3))
    del doc["agent4_send_summary"]
    ai = _ai_doc(_ago(1))
    del ai["agent4_send_summary"]
    import test_sunday_schedule as ts
    ai.update({k: v for k, v in ts._composed_doc().items() if k not in ("started_at",)})
    ai["newsletter_subject"] = "Latent SpaceMail - " + _send_day(ai["started_at"])
    message, healthy = _check(monkeypatch, {"ai": ai, "fin": doc}, mode="draft")
    assert healthy is True, message
    assert "Markets & Money Weekly (finance): run fin" in message and "completed successfully" in message
    assert "spotlight" not in message.split("Other newsletters:")[1]


def test_a_failed_topic_draft_flags_the_email(monkeypatch):
    doc = _topic_doc(_ago(3), agent1b_error="NewsAPI 429")
    for f in ("news_filtered", "news_summaries", "newsletter_composed", "newsletter_variants", "agent4_send_summary"):
        doc.pop(f)
    ai = _ai_doc(_ago(1))
    message, healthy = _check(monkeypatch, {"ai": ai, "fin": doc}, mode="draft")
    assert healthy is False
    section = message.split("Other newsletters:")[1]
    assert "Markets & Money Weekly (finance): run fin" in section and "has problems" in section
    assert "agent1b failed" in section and "NewsAPI 429" in section
    assert "agent3 (compose newsletter) never completed" in section
    assert "scored_papers" not in section and "paper_summaries" not in section   # empty paper stages are fine


def test_a_topic_whose_weekly_job_stopped_is_flagged(monkeypatch):
    message, healthy = _check(monkeypatch, {"ai": _ai_doc(_ago(19)), "fin": _topic_doc(_ago(24 * 8))})
    assert healthy is False and "Markets & Money Weekly (finance): no release run this week" in message


def test_a_topic_with_no_release_run_in_15_days_is_not_expected(monkeypatch):
    message, healthy = _check(monkeypatch, {"ai": _ai_doc(_ago(19)), "fin": _topic_doc(_ago(24 * 20))})
    assert healthy is True and "Other newsletters" not in message


def test_a_failed_topic_send_is_flagged_on_the_send_check(monkeypatch):
    doc = _topic_doc(_ago(21), agent4_send_summary={"total": 3, "sent": 0, "failed": 3})
    message, healthy = _check(monkeypatch, {"ai": _ai_doc(_ago(19)), "fin": doc})
    assert healthy is False and "agent4 ran but sent 0/3 emails" in message


def test_a_broken_ai_run_still_shows_the_topics(monkeypatch):
    message, healthy = _check(monkeypatch, {"ai": _ai_doc(_ago(24 * 7)), "fin": _topic_doc(_ago(21))})
    assert healthy is False and message.startswith("No recent pipeline run found")
    assert "Markets & Money Weekly (finance): run fin" in message


def test_the_styled_email_gives_other_newsletters_their_own_section(monkeypatch):
    import report_html
    message, healthy = _check(monkeypatch, {"ai": _ai_doc(_ago(19)), "fin": _topic_doc(_ago(24 * 8))})
    html = report_html.render(message, healthy, "send")
    assert ">Other newsletters<" in html and ">problem<" in html and "Markets &amp; Money Weekly" in html
    message, healthy = _check(monkeypatch, {"ai": _ai_doc(_ago(19)), "fin": _topic_doc(_ago(21))})
    assert ">all clear<" in report_html.render(message, healthy, "send")


def test_a_bug_in_the_topic_part_does_not_hide_the_ai_report(monkeypatch):
    def boom(rows, now=None):
        raise RuntimeError("bug")

    monkeypatch.setattr(hc, "_expected_topics", boom)
    message, healthy = _check(monkeypatch, {"ai": _ai_doc(_ago(19))})
    assert healthy is False and message.startswith("Pipeline run ai") and "the check failed (RuntimeError" in message
