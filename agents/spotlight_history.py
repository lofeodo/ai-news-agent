# agents/spotlight_history.py
#
# Remembers which papers have already been spotlighted so a paper never appears twice.
# Firestore collection `spotlighted_papers`, doc id = arXiv id without version. In local mode
# (USE_FIRESTORE=false) the same data lives in data/spotlighted_papers.json.
# Failures never fail the agent: a read failure degrades to "no history" (logged), a write failure
# is logged. Writes are keyed by arXiv id and carry the run id, so a retried run just overwrites
# its own record instead of duplicating it.

import json
import os
from datetime import datetime, timezone

COLLECTION = "spotlighted_papers"


def _local_path(data_dir: str) -> str:
    return os.path.join(data_dir, "spotlighted_papers.json")


def load_seen_ids(use_firestore: bool, project: str, data_dir: str, run_id: str | None = None) -> set[str]:
    """arXiv ids spotlighted by earlier runs. Papers recorded by `run_id` itself are excluded, so a
    retry of the same run re-selects instead of treating its own earlier pick as 'seen'."""
    try:
        if use_firestore:
            from google.cloud import firestore
            db = firestore.Client(project=project)
            return {d.id for d in db.collection(COLLECTION).stream()
                    if (d.to_dict() or {}).get("run_id") != run_id}
        path = _local_path(data_dir)
        if not os.path.exists(path):
            return set()
        with open(path, "r", encoding="utf-8") as f:
            return {aid for aid, rec in json.load(f).items() if rec.get("run_id") != run_id}
    except Exception as e:
        print(f"[spotlight] could not load history, continuing without it: {e}", flush=True)
        return set()


def record_spotlight(papers: list[dict], run_id: str, use_firestore: bool, project: str, data_dir: str) -> int:
    """papers: [{arxiv_id, title}]. Returns how many were recorded (0 on error)."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        if use_firestore:
            from google.cloud import firestore
            db = firestore.Client(project=project)
            batch = db.batch()
            for p in papers:
                batch.set(db.collection(COLLECTION).document(p["arxiv_id"]),
                          {"run_id": run_id, "spotlighted_at": now, "title": p.get("title", "")})
            batch.commit()
        else:
            os.makedirs(data_dir, exist_ok=True)
            path = _local_path(data_dir)
            data = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            for p in papers:
                data[p["arxiv_id"]] = {"run_id": run_id, "spotlighted_at": now, "title": p.get("title", "")}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        return len(papers)
    except Exception as e:
        print(f"[spotlight] could not record spotlight: {e}", flush=True)
        return 0
