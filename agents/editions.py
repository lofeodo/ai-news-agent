# agents/editions.py
#
# Editions (contract C2 in docs/plans/newsletter-platform.md): one topic's content for one week, saved by agent3
# as editions/{run_id} (same id as the pipeline_runs doc). Per section: the picks the email shipped, the
# English-only picks when they differ, and the remaining ranked runners-up, every one already summarized. Custom
# newsletters are assembled from editions in code (render.render_from_editions), so the expensive work happens
# once per topic however many newsletters reuse it.

import json
from datetime import datetime, timedelta, timezone

COLLECTION = "editions"
MAX_EDITION_BYTES = 900_000   # well under Firestore's 1 MiB document cap
KEEP_DAYS = 400               # expires_at, for an optional Firestore TTL rule (keep a year of editions)
SCAN_LIMIT = 50               # editions of every topic and kind share the collection; look past a burst

ARTICLE_FIELDS = ("title", "url", "summary", "source", "language", "published_at", "points", "points_source")


def to_article(a: dict) -> dict | None:
    """The C2 article shape, or None when the article has no summary (only summarized articles may appear)."""
    summary = (a.get("summary") or "").strip()
    if not summary or not a.get("url"):
        return None
    points = a.get("hn_score")
    return {
        "title": a.get("title"),
        "url": a.get("url"),
        "summary": summary,
        "source": a.get("source"),
        "language": a.get("language"),
        "published_at": a.get("published_at"),
        "points": points,
        "points_source": "hn" if points is not None else None,
    }


def _articles(raw: list) -> list[dict]:
    out, seen = [], set()
    for a in raw or []:
        art = to_article(a)
        if art and art["url"] not in seen:
            seen.add(art["url"])
            out.append(art)
    return out


def _size(doc: dict) -> int:
    return len(json.dumps(doc, ensure_ascii=False, default=str).encode("utf-8"))


def _trim(doc: dict) -> int:
    """Drop runners-up, last ones of the longest list first, until the doc fits. Returns how many went."""
    dropped = 0
    sections = doc["sections"].values()
    while _size(doc) > MAX_EDITION_BYTES:
        longest = max(sections, key=lambda s: len(s["runners_up"]), default=None)
        if not longest or not longest["runners_up"]:
            break
        longest["runners_up"].pop()
        dropped += 1
    return dropped


def build_edition(topic, run_id: str, run_kind: str, week_of: str, intro: str, selected_all: dict,
                  selected_en: dict, pools: dict, widget_ids, now: datetime | None = None) -> dict:
    """The edition doc for one composed run.

    `selected_all` / `selected_en` are agent3's final picks per section (all languages / English only),
    `pools` the ranked runners-up per section (already free of duplicates of the picks). `week_of` is the
    send date as YYYY-MM-DD.
    """
    now = now or datetime.now(timezone.utc)
    sections = {}
    for c in topic.categories:
        picks = _articles(selected_all.get(c.name))
        section = {"icon": c.icon, "picks": picks}
        picks_en = _articles(selected_en.get(c.name, selected_all.get(c.name)))
        if [a["url"] for a in picks_en] != [a["url"] for a in picks]:
            section["picks_en"] = picks_en
        shipped = {a["url"] for a in picks} | {a["url"] for a in picks_en}
        section["runners_up"] = [a for a in _articles(pools.get(c.name)) if a["url"] not in shipped]
        sections[c.name] = section
    doc = {
        "topic": topic.id,
        "run_id": run_id,
        "run_kind": run_kind,
        "week_of": week_of,
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": now + timedelta(days=KEEP_DAYS),
        "intro": intro,
        "sections": sections,
        "widgets": list(widget_ids),
    }
    dropped = _trim(doc)
    if dropped:
        print(f"  [edition] trimmed {dropped} runner(s)-up to stay under {MAX_EDITION_BYTES:,} bytes")
    return doc


def save(db, edition: dict) -> None:
    db.collection(COLLECTION).document(edition["run_id"]).set(edition)


def _scan(db, limit: int = SCAN_LIMIT) -> list[dict]:
    from google.cloud import firestore
    query = (db.collection(COLLECTION)
             .order_by("created_at", direction=firestore.Query.DESCENDING)
             .limit(limit))
    return [snap.to_dict() for snap in query.stream()]


def latest(db, topic: str) -> dict | None:
    """The newest release edition of a topic, or None. Scans the newest editions by created_at and filters in
    code, so no composite index is needed."""
    return next((e for e in _scan(db) if e.get("topic") == topic and e.get("run_kind") == "release"), None)


def for_week(db, topic: str, week_of: str) -> dict | None:
    """The newest release edition of a topic for one send date (YYYY-MM-DD), or None. A single-field equality
    query (no composite index), sorted in code."""
    rows = [snap.to_dict() for snap in db.collection(COLLECTION).where("week_of", "==", week_of).stream()]
    rows = [e for e in rows if e.get("topic") == topic and e.get("run_kind") == "release"]
    return max(rows, key=lambda e: e.get("created_at") or "", default=None)
