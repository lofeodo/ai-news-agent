# agents/summary_sources.py
#
# Persists the exact source text each summary was written from, so the weekly judge
# (agents/online_judge.py) can check a summary against what the summarizer actually saw instead of
# re-fetching a page that may have changed or an ArXiv PDF the healthcheck can't reach.
#
# One Firestore doc per item (collection `summary_sources`), never on the run doc: the run doc
# has a 1 MiB cap that the full article pools already overflowed once (2026-09-28). Docs carry
# `expires_at` for a Firestore TTL policy, set up once with:
#   gcloud firestore fields ttls update expires_at --collection-group=summary_sources --enable-ttl
# Source text is third-party content: it stays in Firestore and never goes into the public repo.
# A write failure must never fail the summarizing agent, so everything here swallows errors.

import hashlib
from datetime import datetime, timedelta, timezone

COLLECTION = "summary_sources"
TTL_DAYS = 21
_BATCH = 100  # docs per commit; ~9 KB each keeps a commit far under Firestore's 10 MiB limit


def source_key(run_id: str, kind: str, ident: str) -> str:
    return f"{run_id}_{kind}_{hashlib.sha1((ident or '').encode('utf-8')).hexdigest()[:12]}"


def save_sources(db, run_id: str, kind: str, items: list[dict]) -> int:
    """items: [{ident, title, text, used_fallback}]. Returns how many were written (0 on any error)."""
    expires = datetime.now(timezone.utc) + timedelta(days=TTL_DAYS)
    written = 0
    try:
        for i in range(0, len(items), _BATCH):
            batch = db.batch()
            chunk = items[i:i + _BATCH]
            for it in chunk:
                if not it.get("text"):
                    continue
                batch.set(db.collection(COLLECTION).document(source_key(run_id, kind, it["ident"])), {
                    "run_id": run_id, "kind": kind, "ident": it["ident"], "title": it.get("title", ""),
                    "text": it["text"], "used_fallback": bool(it.get("used_fallback")), "expires_at": expires,
                })
                written += 1
            batch.commit()
    except Exception as e:
        print(f"  [sources]  could not persist summary sources ({written} written): {e}", flush=True)
    return written


def load_source(db, run_id: str, kind: str, ident: str) -> dict | None:
    snap = db.collection(COLLECTION).document(source_key(run_id, kind, ident)).get()
    return snap.to_dict() if snap.exists else None
