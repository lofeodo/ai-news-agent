# agents/click_counts.py
#
# Click-count storage for the SendGrid Event Webhook. Two Firestore docs per pipeline run, both aggregate only:
#   click_links/{run_id}  {links: {url_key: {url, title, category}}, sent_at, send_complete, sent_end}
#                         written by agent4 at send time from the run doc (agents/click_links.py);
#                         sent_at = first send start (kept on a re-run), sent_end = last send finished
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


MAX_SEND_WINDOW_SECONDS = 6 * 3600  # a send that never reports completion counts as running this long at most


def save_link_doc(db, run_id: str, link_map: dict, sent_at: int) -> None:
    """Store the link map when a send starts. A re-run keeps the original start time."""
    ref = db.collection(LINKS_COLLECTION).document(run_id)
    snap = ref.get()
    first = ((snap.to_dict() or {}).get("sent_at") if snap.exists else None)
    ref.set({"links": link_map, "sent_at": int(first if first is not None else sent_at), "send_complete": False})


def mark_send_complete(db, run_id: str, ended_at: int) -> None:
    """Record when the last email went out, so scanner prefetches of late recipients still count as early."""
    db.collection(LINKS_COLLECTION).document(run_id).set({"sent_end": int(ended_at), "send_complete": True}, merge=True)


def load_link_doc(db, run_id: str) -> dict | None:
    snap = db.collection(LINKS_COLLECTION).document(run_id).get()
    return snap.to_dict() if snap.exists else None


def bucket_for(event: dict, sent_at, sent_end=None, send_complete=None) -> str:
    """bots: automated user agent. early: before CLICK_EARLY_SECONDS after the send ended (mail scanners
    prefetch links at delivery, and delivery spans the whole send). While a send is still running, that is
    anything within the send window. clicks: everything else, the figure the weekly report headlines.
    Docs without the window fields behave as before: early is measured from the send start."""
    if event.get("bot"):
        return "bots"
    ts = event.get("timestamp")
    if ts is None or sent_at is None:
        return "clicks"
    if sent_end is not None:
        window_end = sent_end
    elif send_complete is False:
        window_end = sent_at + MAX_SEND_WINDOW_SECONDS
    else:
        window_end = sent_at
    return "early" if ts - window_end < config.CLICK_EARLY_SECONDS else "clicks"


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
        bucket = bucket_for(ev, doc.get("sent_at"), doc.get("sent_end"), doc.get("send_complete"))
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
