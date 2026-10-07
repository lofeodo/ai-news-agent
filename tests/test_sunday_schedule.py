from datetime import datetime, timezone

import pytest

import config
import agent_healthcheck as hc


def utc(y, m, d, h):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def test_sunday_draft_is_dated_monday():
    # Sunday 2026-10-11 12:00 Toronto (EDT) = 16:00 UTC
    assert config.newsletter_send_date(utc(2026, 10, 11, 16)).strftime("%B %d, %Y") == "October 12, 2026"


def test_monday_and_midweek_runs_keep_their_own_date():
    assert config.newsletter_send_date(utc(2026, 10, 12, 10)).strftime("%B %d, %Y") == "October 12, 2026"
    assert config.newsletter_send_date(utc(2026, 10, 14, 15)).strftime("%B %d, %Y") == "October 14, 2026"


def test_late_sunday_evening_toronto_still_monday():
    # Sunday 11 PM Toronto is already Monday 03:00 UTC; local date is still Sunday -> Monday
    assert config.newsletter_send_date(utc(2026, 10, 12, 3)).strftime("%B %d, %Y") == "October 12, 2026"


def test_mode_explicit_check_wins_else_weekday():
    assert hc._resolve_mode("send", utc(2026, 10, 11, 18)) == "send"
    assert hc._resolve_mode("draft", utc(2026, 10, 12, 11)) == "draft"
    assert hc._resolve_mode(None, utc(2026, 10, 11, 18)) == "draft"
    assert hc._resolve_mode(None, utc(2026, 10, 12, 11)) == "send"
    with pytest.raises(ValueError):
        hc._resolve_mode("weekly")


def _composed_doc():
    html = "<html>" + "x" * 6000 + "{{UNSUBSCRIBE_URL}} {{PREFERENCES_URL}}</html>"
    doc = {f: 1 for f, _ in hc.EXPECTED_STAGES if f != "agent4_send_summary"}
    doc.update(newsletter_variants={k: html for k in hc.VARIANT_KEYS},
               newsletter_subject="Latent SpaceMail - October 12, 2026",
               started_at="2026-10-11T16:00:12+00:00")
    return doc


def test_variant_keys_match_agent3():
    import agent3_compose
    assert set(hc.VARIANT_KEYS) == set(agent3_compose.NEWSLETTER_VARIANTS)


def test_draft_mode_ignores_missing_send_stage_and_passes_good_draft():
    doc = _composed_doc()
    assert hc._diagnose(doc) != []                      # send mode: agent4 never sent
    assert hc._diagnose(doc, "draft") == []


def test_draft_mode_catches_bad_composition():
    doc = _composed_doc()
    del doc["newsletter_variants"]["1_1"]
    doc["newsletter_variants"]["0_1"] = "<html>tiny</html>"
    doc["newsletter_subject"] = "Latent SpaceMail - October 11, 2026"  # drafting date, not send date
    text = " | ".join(hc._diagnose(doc, "draft"))
    assert "1_1 is missing" in text
    assert "0_1 is only" in text and "placeholder" in text
    assert "not dated for the send day (October 12, 2026)" in text


def test_send_mode_does_not_check_composition():
    doc = _composed_doc()
    doc["agent4_send_summary"] = {"total": 3, "sent": 3, "failed": 0}
    doc["newsletter_variants"] = {"0_0": "tiny"}
    assert hc._diagnose(doc, "send") == []
