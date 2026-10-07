from datetime import datetime, timezone

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


def test_draft_check_only_on_sunday():
    assert hc._is_draft_check(utc(2026, 10, 11, 18))
    assert not hc._is_draft_check(utc(2026, 10, 12, 11))


def test_draft_mode_ignores_missing_send_stage():
    doc = {f: 1 for f, _ in hc.EXPECTED_STAGES if f != "agent4_send_summary"}
    assert hc._diagnose(doc) != []
    assert hc._diagnose(doc, draft_only=True) == []
