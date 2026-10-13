"""Read-only: count subscribers by the state of their link token. Prints counts only, no addresses.

Active subscribers whose token is missing get no newsletter (agent4 skips them); an expired date on an active
subscriber is harmless (their links keep working) but is counted so we can see how many there are.
Nothing is written to Firestore.

CMD:  python -m evals.report_subscriber_tokens   (needs Application Default Credentials; GCP_PROJECT_ID or gcloud default)
"""
import os
from datetime import datetime, timezone


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if getattr(dt, "tzinfo", None) is None else dt


def summarize(docs, now=None):
    """Counts for an iterable of subscriber dicts."""
    now = now or datetime.now(timezone.utc)
    out = {"total": 0, "active": 0, "active_missing_token": 0, "active_past_expiry": 0,
           "active_no_expiry_date": 0, "pending_past_expiry": 0}
    for d in docs:
        out["total"] += 1
        expires = d.get("token_expires_at")
        expired = bool(expires) and _aware(expires) < now
        if d.get("active"):
            out["active"] += 1
            out["active_missing_token"] += not d.get("token")
            out["active_past_expiry"] += expired
            out["active_no_expiry_date"] += not expires
        else:
            out["pending_past_expiry"] += expired
    return out


def main():
    from google.cloud import firestore
    db = firestore.Client(project=os.environ.get("GCP_PROJECT_ID") or "ai-news-letter-497720")
    counts = summarize(d.to_dict() for d in db.collection("subscribers").stream())
    for key, value in counts.items():
        print(f"{key:24s} {value}")


if __name__ == "__main__":
    main()
