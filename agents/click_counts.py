# agents/click_counts.py
#
# Click-count storage for the SendGrid Event Webhook. Two Firestore docs per pipeline run, both aggregate only:
#   click_links/{run_id}  {links: {url_key: {url, title, category}}, sent_at: epoch seconds}
#                         written by agent4 at send time from the run doc (agents/click_links.py)
#   click_counts/{run_id} {clicks|early|bots: {url_key: n}} atomic increments written by the webhook route
# No email, IP, user agent or message id is ever stored; reduce_events() (agents/sendgrid_webhook.py) has
# already dropped them before anything here sees an event.

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import click_links

LINKS_COLLECTION = "click_links"
COUNTS_COLLECTION = "click_counts"
BUCKETS = ("clicks", "early", "bots")


def save_link_doc(db, run_id: str, link_map: dict, sent_at: int) -> None:
    db.collection(LINKS_COLLECTION).document(run_id).set({"links": link_map, "sent_at": int(sent_at)})


def load_link_doc(db, run_id: str) -> dict | None:
    snap = db.collection(LINKS_COLLECTION).document(run_id).get()
    return snap.to_dict() if snap.exists else None


def bucket_for(event: dict, sent_at) -> str:
    """bots: automated user agent. early: within CLICK_EARLY_SECONDS of the send start (mail scanners
    prefetch links at delivery). clicks: everything else, the figure the weekly report headlines."""
    if event.get("bot"):
        return "bots"
    ts = event.get("timestamp")
    if ts is not None and sent_at is not None and ts - sent_at < config.CLICK_EARLY_SECONDS:
        return "early"
    return "clicks"


def tally(events: list[dict], get_link_doc) -> dict[str, dict[str, dict[str, int]]]:
    """{run_id: {bucket: {url_key: n}}} for events whose URL is one we shipped in that run.

    `get_link_doc(run_id)` returns the click_links doc or None. Clicks on anything else (footer,
    unsubscribe, preferences) and clicks for runs with no link doc are ignored.
    """
    out: dict = {}
    docs: dict = {}
    for ev in events:
        run_id = ev["run_id"]
        if run_id not in docs:
            docs[run_id] = get_link_doc(run_id)
        doc = docs[run_id]
        if not doc:
            continue
        hit = click_links.lookup(doc.get("links") or {}, ev["url"])
        if not hit:
            continue
        key, _ = hit
        bucket = bucket_for(ev, doc.get("sent_at"))
        counts = out.setdefault(run_id, {}).setdefault(bucket, {})
        counts[key] = counts.get(key, 0) + 1
    return out


def apply_tallies(db, tallies: dict) -> int:
    """Add the tallies to click_counts with atomic increments (one write per run). Returns clicks added."""
    from google.cloud import firestore

    added = 0
    for run_id, buckets in tallies.items():
        data = {b: {k: firestore.Increment(n) for k, n in counts.items()} for b, counts in buckets.items()}
        db.collection(COUNTS_COLLECTION).document(run_id).set(data, merge=True)
        added += sum(n for counts in buckets.values() for n in counts.values())
    return added


def load_counts(db, run_id: str) -> dict:
    """{bucket: {url_key: n}} for a run, empty buckets filled in."""
    snap = db.collection(COUNTS_COLLECTION).document(run_id).get()
    data = snap.to_dict() if snap.exists else {}
    return {b: dict((data or {}).get(b) or {}) for b in BUCKETS}
