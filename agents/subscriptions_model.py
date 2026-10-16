# agents/subscriptions_model.py
#
# Which newsletters one subscriber doc (subscribers/{email}) is subscribed to. Contract C1 in
# docs/plans/newsletter-platform.md:
#
#   newsletters:      ["ai", "finance", ...]   the newsletters this address actively gets
#   newsletter_meta:  {id: {"subscribed_at", "consent_text_version", "unsubscribed_at"?}}
#   active:           the legacy flag, still "subscribed to the AI newsletter" (agent4's AI query uses it)
#   subscribed_any:   True while the address gets at least one newsletter (the headcount cap counts it)
#
# A doc written before newsletters existed has only `active`; it reads as ["ai"] when active. `active` stays the
# source of truth for AI even on a doc that has the list, so a doc changed by code that only knows `active` (an
# older deploy, a manual fix) still means what agent4 will do. Every change goes through add()/remove(), which
# return the fields to write and keep all four in step.

from datetime import datetime, timezone

AI = "ai"

# Which consent wording a signup form showed. A form that sends no version is the AI-only form from before
# per-newsletter signup existed. Bump CURRENT (and add it to KNOWN) when the wording on a signup form changes.
CONSENT_LEGACY = "ai-form-pre-2026-10"
CONSENT_CURRENT = "2026-10-16"
KNOWN_CONSENT_VERSIONS = {CONSENT_LEGACY, CONSENT_CURRENT}

# The words every current signup form shows next to the newsletter names (kept here so the consent record and
# the pages agree; the pages carry the same text).
CONSENT_TEXT = ("You'll get the newsletters you picked by email every Monday. Each email has a link to "
                "unsubscribe from that newsletter or from everything.")


def consent_version(value) -> str:
    """The version to record for a signup request: the one the form sent if it is known, otherwise legacy."""
    return value if isinstance(value, str) and value in KNOWN_CONSENT_VERSIONS else CONSENT_LEGACY


def subscribed_ids(doc: dict | None) -> list[str]:
    """The newsletters this doc is subscribed to, AI first, no duplicates."""
    d = doc or {}
    raw = d.get("newsletters")
    others = [i for i in dict.fromkeys(raw) if isinstance(i, str) and i and i != AI] if isinstance(raw, list) else []
    return ([AI] if d.get("active") else []) + others


def is_subscribed(doc: dict | None, newsletter_id: str) -> bool:
    return newsletter_id in subscribed_ids(doc)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _fields(doc: dict | None, ids: list[str], meta: dict) -> dict:
    return {
        "newsletters":     ids,
        "newsletter_meta": meta,
        "active":          AI in ids,
        "subscribed_any":  bool(ids),
    }


def add(doc: dict | None, newsletter_id: str, consent_text_version: str, now: datetime | None = None) -> dict:
    """Fields to write so `doc` is subscribed to `newsletter_id`.

    newsletter_meta records when and under which consent wording this subscription was made; re-subscribing
    replaces the entry (the newest consent is the one that counts).
    """
    ids = subscribed_ids(doc)
    if newsletter_id not in ids:
        ids = ids + [newsletter_id]
    ids = ([AI] if AI in ids else []) + [i for i in ids if i != AI]
    meta = dict((doc or {}).get("newsletter_meta") or {})
    meta[newsletter_id] = {"subscribed_at": _now(now), "consent_text_version": consent_text_version}
    return _fields(doc, ids, meta)


def remove(doc: dict | None, newsletter_id: str, now: datetime | None = None) -> dict:
    """Fields to write so `doc` is no longer subscribed to `newsletter_id`. The consent record stays, with the
    time of the unsubscribe added."""
    ids = [i for i in subscribed_ids(doc) if i != newsletter_id]
    meta = dict((doc or {}).get("newsletter_meta") or {})
    if newsletter_id in meta:
        meta[newsletter_id] = {**meta[newsletter_id], "unsubscribed_at": _now(now)}
    return _fields(doc, ids, meta)


def remove_all(doc: dict | None, now: datetime | None = None) -> dict:
    """Fields to write so `doc` gets no newsletter at all."""
    current = dict(doc or {})
    for i in subscribed_ids(doc):
        current.update(remove(current, i, now))
    return _fields(current, [], dict(current.get("newsletter_meta") or {}))
