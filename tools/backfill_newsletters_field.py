# tools/backfill_newsletters_field.py
#
# One-off: give every existing subscriber doc the per-newsletter fields of contract C1 (`newsletters`,
# `subscribed_any`, and a `newsletter_meta.ai` consent record for AI subscribers), derived from the legacy
# `active` flag. The code reads legacy docs correctly without this (agents/subscriptions_model.py); the backfill
# makes the data uniform so queries and exports see one shape. Safe to run more than once: a doc that already
# matches is left alone, and `active` is never changed.
#
#   python tools/backfill_newsletters_field.py            # dry run: prints counts only
#   python tools/backfill_newsletters_field.py --apply    # writes it
#
# Prints counts only, never an address. Needs Application Default Credentials for the project
# (gcloud auth application-default login).
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents"))
import subscriptions_model as sm  # noqa: E402

COLLECTION = "subscribers"
FIELDS = ["active", "newsletters", "subscribed_any", "newsletter_meta", "confirmed_at", "subscribed_at"]


def plan_update(doc: dict) -> dict:
    """Fields to write on one subscriber doc, or {} when it already has the C1 shape."""
    ids = sm.subscribed_ids(doc)
    fields = {}
    if doc.get("newsletters") != ids:
        fields["newsletters"] = ids
    if doc.get("subscribed_any") is not bool(ids):
        fields["subscribed_any"] = bool(ids)
    meta = doc.get("newsletter_meta") or {}
    if sm.AI in ids and sm.AI not in meta:
        fields["newsletter_meta"] = {**meta, sm.AI: {
            "subscribed_at": doc.get("confirmed_at") or doc.get("subscribed_at"),
            "consent_text_version": sm.CONSENT_LEGACY,
            "backfilled": True,
        }}
    return fields


def plan(rows: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    return [(doc_id, f) for doc_id, f in ((i, plan_update(d)) for i, d in rows) if f]


def run(db, apply: bool) -> dict:
    rows = [(d.id, d.to_dict() or {}) for d in db.collection(COLLECTION).select(FIELDS).stream()]
    updates = plan(rows)
    if apply:
        for doc_id, fields in updates:
            db.collection(COLLECTION).document(doc_id).update(fields)
    return {
        "docs": len(rows),
        "to_update": len(updates),
        "ai_subscribers": sum(1 for _, d in rows if d.get("active")),
        "subscribed_to_any": sum(1 for _, d in rows if sm.subscribed_ids(d)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Add the per-newsletter fields to existing subscriber docs.")
    ap.add_argument("--apply", action="store_true", help="write the fields (default is a dry run)")
    args = ap.parse_args()

    from google.cloud import firestore

    counts = run(firestore.Client(), args.apply)
    print(f"{counts['docs']} subscriber doc(s): {counts['ai_subscribers']} on the AI newsletter, "
          f"{counts['subscribed_to_any']} subscribed to at least one newsletter")
    verb = "updated" if args.apply else "would be updated (dry run; pass --apply)"
    print(f"{counts['to_update']} doc(s) {verb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
