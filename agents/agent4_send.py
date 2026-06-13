# agents/agent4_send.py
#
# Reads the composed newsletter HTML from Firestore (cloud) or local file (local),
# then sends it via SendGrid.
#
# Cloud mode: queries the `subscribers` collection for active subscribers and
# sends a personalized copy to each one (per-subscriber unsubscribe + preferences
# links substituted into the footer placeholders embedded by agent3).
#
# Local mode: reads data/newsletter.html and sends a single copy to
# TEST_RECIPIENT_EMAIL (if set). Local mode does NOT query Firestore.
#
# Triggered by a Cloud Scheduler job at 7:00 AM every Monday — separate from the
# pipeline orchestrator, which runs at 6:00 AM.

import json
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    DATA_DIR,
    GCP_PROJECT_ID,
    USE_FIRESTORE,
    FIRESTORE_COLLECTION,
    SUBSCRIBERS_COLLECTION,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEWSLETTER_NAME      = "Latent SpaceMail"
SENDER_EMAIL         = "newsletter@lofeodo.com"
SENDER_NAME          = "Latent SpaceMail"
SENDGRID_SECRET_NAME = "sendgrid-api-key"
USE_SECRET_MANAGER   = os.environ.get("USE_SECRET_MANAGER", "false").lower() == "true"

# Public base URL of the agent-subscriptions service — used to build each
# subscriber's one-click unsubscribe link. Must match the value set on the
# agent-subscriptions service.
SERVICE_BASE_URL  = os.environ.get("SERVICE_BASE_URL", "").rstrip("/")

# Base URL of the Firebase Hosting frontend — used for the preferences magic
# link. Unset until the frontend exists (Phase 11); until then the prefs link
# is malformed, but we still substitute the placeholder so the raw
# "{{PREFERENCES_URL}}" string never ships in an email.
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "").rstrip("/")

# Local-mode only: send a single copy here. No effect in cloud mode.
TEST_RECIPIENT_EMAIL = os.environ.get("TEST_RECIPIENT_EMAIL", "")

# Cloud-mode test override: if set, skip the Firestore subscriber list and
# send only to this address. Used for test-send jobs that must not reach
# real subscribers.
TEST_SEND_TO = os.environ.get("TEST_SEND_TO", "")


# ---------------------------------------------------------------------------
# SendGrid
# ---------------------------------------------------------------------------

def _get_sendgrid_api_key() -> str:
    """Load SendGrid API key from Secret Manager (cloud) or env var (local)."""
    if USE_SECRET_MANAGER:
        from google.cloud import secretmanager
        client   = secretmanager.SecretManagerServiceClient()
        name     = f"projects/{GCP_PROJECT_ID}/secrets/{SENDGRID_SECRET_NAME}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8").strip()
    else:
        key = os.environ.get("SENDGRID_API_KEY", "")
        if not key:
            raise RuntimeError("SENDGRID_API_KEY env var not set for local mode")
        return key


def send_email(api_key: str, to_email: str, html_body: str, subject: str) -> None:
    """Send one email. Raises on non-2xx (urllib raises on 4xx/5xx)."""
    import urllib.request
    payload = json.dumps({
        "personalizations": [{"to": [{"email": to_email}]}],
        "from":    {"email": SENDER_EMAIL, "name": SENDER_NAME},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_body}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        print(f"[sendgrid]  sent to {to_email} — status {resp.status}", flush=True)


# ---------------------------------------------------------------------------
# Personalization
# ---------------------------------------------------------------------------

def _personalize(html: str, token: str) -> str:
    """Substitute the per-subscriber footer placeholders embedded by agent3.

    Both placeholders must be replaced (option-A contract). The preferences
    link depends on FRONTEND_BASE_URL — malformed until the frontend exists,
    but we still substitute so the raw placeholder never ships.
    """
    unsub_link = f"{SERVICE_BASE_URL}/unsubscribe?token={token}"
    prefs_link = f"{FRONTEND_BASE_URL}/preferences.html?token={token}"
    html = html.replace("{{UNSUBSCRIBE_URL}}", unsub_link)
    html = html.replace("{{PREFERENCES_URL}}", prefs_link)
    return html


# ---------------------------------------------------------------------------
# Newsletter lookup
# ---------------------------------------------------------------------------

def _variant_key(prefs: dict) -> str:
    """Map a subscriber prefs dict to its newsletter variant key."""
    fr = "1" if prefs.get("include_french", False) else "0"
    ca = "1" if prefs.get("include_canada", False) else "0"
    return f"{fr}_{ca}"


def _load_latest_newsletter(db):
    """Return (variants, subject) for the most recent run with newsletter_html set.

    variants is a dict keyed by "0_0" / "1_0" / "0_1" / "1_1".
    Falls back to {"0_0": newsletter_html} for old runs that predate variants.
    """
    from google.cloud import firestore as _fs
    # Most recent run doc that has newsletter_html set.
    # Agent4 is triggered by Scheduler independently — it doesn't receive a run_id.
    results = (
        db.collection(FIRESTORE_COLLECTION)
        .where("newsletter_html", "!=", None)
        .order_by("newsletter_html")   # required by Firestore for != queries
        .order_by("started_at", direction=_fs.Query.DESCENDING)
        .limit(1)
        .stream()
    )
    docs = list(results)
    if not docs:
        raise RuntimeError("[agent4]  No Firestore document found with newsletter_html set")
    data     = docs[0].to_dict()
    variants = data.get("newsletter_variants")
    if not variants:
        variants = {"0_0": data.get("newsletter_html", "")}
    subject  = data.get("newsletter_subject") or f"{NEWSLETTER_NAME} — {datetime.now().strftime('%B %d, %Y')}"
    print(f"[agent4]  Loaded newsletter variants from Firestore "
          f"(run_id={data.get('run_id')}, keys={list(variants.keys())})", flush=True)
    return variants, subject


def _active_subscribers(db) -> list[dict]:
    """Return a list of active subscriber dicts (must include email + token)."""
    docs = (
        db.collection(SUBSCRIBERS_COLLECTION)
        .where("active", "==", True)
        .stream()
    )
    subs = [d.to_dict() for d in docs]
    return subs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(run_id: str):
    """Read newsletter HTML and send via SendGrid.

    Cloud mode: per-subscriber send loop over active subscribers.
    Local mode: single send to TEST_RECIPIENT_EMAIL (no Firestore).
    Always exits without raising on per-send failures; logs a structured
    send_summary line for Cloud Logging.
    """
    start_time = datetime.now()

    # -------------------------------------------------------------------
    # LOCAL MODE — single send to TEST_RECIPIENT_EMAIL, no Firestore.
    # -------------------------------------------------------------------
    if not USE_FIRESTORE:
        variant_path = os.path.join(DATA_DIR, "newsletter_0_0.html")
        legacy_path  = os.path.join(DATA_DIR, "newsletter.html")
        html_path    = variant_path if os.path.exists(variant_path) else legacy_path
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        subject = f"{NEWSLETTER_NAME} — {datetime.now().strftime('%B %d, %Y')}"
        print(f"[agent4]  Loaded newsletter HTML from {html_path}", flush=True)

        if not TEST_RECIPIENT_EMAIL:
            print("  [warn]  TEST_RECIPIENT_EMAIL not set — skipping send (local mode)", flush=True)
            return

        # In local mode there is no subscriber token; substitute empty-ish
        # links so the placeholders don't ship raw. (Local test only.)
        api_key = _get_sendgrid_api_key()
        local_html = _personalize(html, token="LOCAL-TEST")
        try:
            send_email(api_key, TEST_RECIPIENT_EMAIL, local_html, subject)
            print("[agent4]  local test send OK", flush=True)
        except Exception as e:
            print(f"[agent4]  local test send FAILED: {e}", flush=True)
        return

    # -------------------------------------------------------------------
    # CLOUD MODE — per-subscriber send loop.
    # -------------------------------------------------------------------
    from google.cloud import firestore as _fs
    db = _fs.Client(project=GCP_PROJECT_ID)

    variants, subject = _load_latest_newsletter(db)

    if TEST_SEND_TO:
        print(f"[agent4]  TEST_SEND_TO override — sending only to {TEST_SEND_TO}", flush=True)
        subscribers = [{"email": TEST_SEND_TO, "token": "TEST-SEND"}]
    else:
        subscribers = _active_subscribers(db)
    print(f"[agent4]  {len(subscribers)} active subscriber(s)", flush=True)

    api_key = _get_sendgrid_api_key()

    sent     = 0
    failed   = 0
    failures = []  # list of {email, error}

    for sub in subscribers:
        email = sub.get("email")
        token = sub.get("token")
        if not email or not token:
            # Defensive: a malformed doc shouldn't exist, but never let it
            # abort the loop. Skip and record.
            failed += 1
            failures.append({"email": email or "<missing>", "error": "missing email or token"})
            print(f"  [warn]  skipping malformed subscriber doc: {sub}", flush=True)
            continue

        prefs = sub.get("prefs", {})
        key   = _variant_key(prefs)
        html  = variants.get(key) or variants.get("0_0", "")
        if not html:
            failed += 1
            failures.append({"email": email, "error": f"no HTML for variant {key}"})
            print(f"  [warn]  no HTML for variant {key}, skipping {email}", flush=True)
            continue

        personalized = _personalize(html, token)
        try:
            send_email(api_key, email, personalized, subject)
            sent += 1
        except Exception as e:
            failed += 1
            failures.append({"email": email, "error": str(e)})
            print(f"  [error]  send failed for {email}: {e}", flush=True)
            # Continue — one bad send must not abort the rest.

    elapsed = (datetime.now() - start_time).total_seconds()

    # Structured summary line — Cloud Logging parses stdout JSON into
    # jsonPayload, making send history queryable:
    #   gcloud logging read 'jsonPayload.event="send_summary"' ...
    summary = {
        "event":     "send_summary",
        "service":   "agent4",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total":     len(subscribers),
        "sent":      sent,
        "failed":    failed,
        "failures":  failures,
        "subject":   subject,
        "elapsed_s": round(elapsed, 1),
    }
    print(json.dumps(summary), flush=True)

    # Human-readable tail for the live log view.
    print(f"\n--- agent4 done in {elapsed:.1f}s — sent {sent}, failed {failed} ---", flush=True)
    # Always exit 0 (no raise): a nonzero exit would just create Scheduler
    # noise with no useful retry behavior. Failures are logged loudly above.


if __name__ == "__main__":
    run(run_id="local-debug")