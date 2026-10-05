"""Loads per-run distribution summaries for drift monitoring from Firestore.

Read-only. Takes a Firestore client so tests can pass a fake.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import FIRESTORE_COLLECTION

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


def load_baseline(db, current_run_id: str, n_baseline: int) -> list[dict]:
    """Up to `n_baseline` usable summaries from the runs started before the current one."""
    from google.cloud import firestore

    # Over-fetch: some prior runs will be single_pass, failed early, or lack data.
    docs = (
        db.collection(FIRESTORE_COLLECTION)
        .order_by("started_at", direction=firestore.Query.DESCENDING)
        .limit(n_baseline * 3 + 1)
        .stream()
    )
    out = []
    for d in docs:
        if d.id == current_run_id:
            continue
        summary = run_summary(db, d.id, d.to_dict() or {})
        if summary:
            out.append(summary)
        if len(out) >= n_baseline:
            break
    return out
