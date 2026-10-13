# agents/mail_throttle.py
#
# Pacing for the public endpoints that send mail to an address the caller names. State lives in Firestore so every Cloud Run instance sees the same counts:
#   mail_throttle/{sha256(email)}  last_at, day, count, expires_at   (no raw address)
#   mail_budget/{YYYY-MM-DD}       mail_count, expires_at
# A refused call is the caller's cue to skip the send and still answer exactly as for a sent one.

import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

THROTTLE_COLLECTION = "mail_throttle"
BUDGET_COLLECTION   = "mail_budget"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _limits() -> dict:
    return {
        "cooldown":    _int_env("MAIL_COOLDOWN_SECONDS", 60),
        "per_address": _int_env("MAIL_MAX_PER_ADDRESS_DAILY", 5),
        "mail_ceiling": _int_env("MAIL_DAILY_CEILING", 1000),
    }


def address_key(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def decide_mail(addr: dict, budget: dict, now: datetime, limits: dict) -> tuple[bool, str]:
    """Pure decision for one mail. `addr` and `budget` are the stored docs (empty when absent). Returns (allowed, reason)."""
    day = now.strftime("%Y-%m-%d")
    if budget.get("mail_count", 0) >= limits["mail_ceiling"]:
        return False, "daily_ceiling"
    if addr.get("day") == day and addr.get("count", 0) >= limits["per_address"]:
        return False, "address_daily"
    last = addr.get("last_at")
    if last is not None:
        if getattr(last, "tzinfo", None) is None:
            last = last.replace(tzinfo=timezone.utc)
        if (now - last).total_seconds() < limits["cooldown"]:
            return False, "cooldown"
    return True, "ok"


def allow_mail(db, email: str, now: datetime | None = None) -> bool:
    """Atomically record one mail to this address if it is within the limits. False means: do not send."""
    from google.cloud import firestore

    now = now or datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    limits = _limits()
    addr_ref   = db.collection(THROTTLE_COLLECTION).document(address_key(email))
    budget_ref = db.collection(BUDGET_COLLECTION).document(day)

    @firestore.transactional
    def _take(transaction):
        addr   = addr_ref.get(transaction=transaction).to_dict() or {}
        budget = budget_ref.get(transaction=transaction).to_dict() or {}
        ok, reason = decide_mail(addr, budget, now, limits)
        if not ok:
            return False, reason
        count = addr.get("count", 0) + 1 if addr.get("day") == day else 1
        transaction.set(addr_ref, {"last_at": now, "day": day, "count": count,
                                   "expires_at": now + timedelta(days=2)})
        transaction.set(budget_ref, {"mail_count": budget.get("mail_count", 0) + 1,
                                     "expires_at": now + timedelta(days=3)})
        return True, "ok"

    try:
        ok, reason = _take(db.transaction())
    except Exception as e:
        # A throttle failure must not take sign-up down with it; the per-IP limiter still applies.
        print(f"[mail_throttle]  check failed ({type(e).__name__}), allowing", flush=True)
        return True
    if not ok:
        level = "ERROR" if reason == "daily_ceiling" else "INFO"
        print(f"[mail_throttle]  {level} mail skipped: {reason}", flush=True)
    return ok
