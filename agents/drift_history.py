"""Loads per-run distribution summaries for drift monitoring from Firestore.

Read-only. Takes a Firestore client so tests can pass a fake.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import FIRESTORE_COLLECTION

import topics
from drift import summarize_audit

AUDIT_COLLECTION = "agent1b_audits"


def run_summary(db, run_id: str, doc: dict) -> dict | None:
    """Distribution summary for one run, or None if it has no graph-mode data.

    Newer runs carry `confidence_hist` and `category_counts` on `agent1b_review_summary`.
    Older graph-mode runs only have the audit doc, so the same summary is computed from
    it. single_pass runs and runs that never reached agent1b have neither and are skipped.
    """
    review = doc.get("agent1b_review_summary") or {}
    if not review:
        return None
    if "confidence_hist" not in review:
        snap = db.collection(AUDIT_COLLECTION).document(run_id).get()
        if not snap.exists:
            return None
        review = {**review, **summarize_audit((snap.to_dict() or {}).get("articles", []))}
    return {
        "run_id": run_id,
        "confidence_hist": review["confidence_hist"],
        "category_counts": review["category_counts"],
        "articles": review.get("articles", 0),
        "routed_to_review": review.get("routed_to_review", 0),
    }


# Other topics' runs (mostly debug) share the collection; scan this many extra rows past the ones wanted so a
# burst of them cannot push the AI runs out of the window.
_OTHER_TOPIC_HEADROOM = 50


def _ai_run_ids(db, current_run_id: str, wanted: int) -> list[str]:
    """Ids of up to `wanted` AI-newsletter runs started before the current one, newest first.

    Scans only the small `topic` field; callers fetch the full docs (which can be close to 1 MiB) for the
    ids they actually use.
    """
    from google.cloud import firestore

    scan = (
        db.collection(FIRESTORE_COLLECTION)
        .order_by("started_at", direction=firestore.Query.DESCENDING)
        .select(["topic"])
        .limit(wanted + 1 + _OTHER_TOPIC_HEADROOM)
        .stream()
    )
    ids = [d.id for d in scan if d.id != current_run_id and topics.is_default_topic(d.to_dict())]
    return ids[:wanted]


def recent_runs(db, current_run_id: str, n: int) -> list[tuple[str, dict]]:
    """(run_id, doc) for up to `n` AI runs started before the current one, newest first."""
    return [(i, db.collection(FIRESTORE_COLLECTION).document(i).get().to_dict() or {})
            for i in _ai_run_ids(db, current_run_id, n)]


def load_baseline(db, current_run_id: str, n_baseline: int) -> list[dict]:
    """Up to `n_baseline` usable summaries from the AI runs started before the current one."""
    out = []
    # Over-fetch: some prior runs will be single_pass, failed early, or lack data.
    for run_id in _ai_run_ids(db, current_run_id, n_baseline * 3):
        doc = db.collection(FIRESTORE_COLLECTION).document(run_id).get().to_dict() or {}
        summary = run_summary(db, run_id, doc)
        if summary:
            out.append(summary)
        if len(out) >= n_baseline:
            break
    return out
