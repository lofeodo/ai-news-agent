from datetime import datetime, timedelta, timezone

from evals.report_subscriber_tokens import summarize

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


def test_counts_by_state():
    docs = [
        {"active": True, "token": "a", "token_expires_at": NOW + timedelta(days=1)},
        {"active": True, "token": "b", "token_expires_at": NOW - timedelta(days=1)},
        {"active": True, "token": "c", "token_expires_at": (NOW - timedelta(days=1)).replace(tzinfo=None)},
        {"active": True, "token": None, "token_expires_at": None},
        {"active": False, "token": "d", "token_expires_at": NOW - timedelta(days=1)},
        {"active": False, "token": "e", "token_expires_at": NOW + timedelta(days=1)},
    ]
    assert summarize(docs, now=NOW) == {
        "total": 6, "active": 4, "active_missing_token": 1, "active_past_expiry": 2,
        "active_no_expiry_date": 1, "pending_past_expiry": 1,
    }
