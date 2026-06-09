# agents/agent4_send.py
#
# Reads the composed newsletter HTML from Firestore (cloud) or local file (local),
# then sends it via SendGrid.
#
# Triggered by a Cloud Scheduler job at 7:00 AM every Monday — separate from the
# pipeline orchestrator, which runs at 6:00 AM.

import json
import os
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_DIR, GCP_PROJECT_ID, USE_FIRESTORE, FIRESTORE_COLLECTION

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEWSLETTER_NAME      = "Latent SpaceMail"
RECIPIENT_EMAIL      = os.environ.get("NEWSLETTER_RECIPIENT_EMAIL", "")
SENDER_EMAIL         = "latentspacemail@gmail.com"
SENDER_NAME          = "Latent SpaceMail"
SENDGRID_SECRET_NAME = "sendgrid-api-key"
USE_SECRET_MANAGER   = os.environ.get("USE_SECRET_MANAGER", "false").lower() == "true"


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


def send_email(api_key: str, html_body: str, subject: str) -> None:
    import urllib.request
    payload = json.dumps({
        "personalizations": [{"to": [{"email": RECIPIENT_EMAIL}]}],
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
        print(f"[sendgrid]  sent to {RECIPIENT_EMAIL} — status {resp.status}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(run_id: str):
    """Read newsletter HTML and send via SendGrid."""
    start_time = datetime.now()

    if USE_FIRESTORE:
        from google.cloud import firestore as _fs
        db = _fs.Client(project=GCP_PROJECT_ID)
        # Query for the most recent run doc that has newsletter_html set.
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
        data    = docs[0].to_dict()
        html    = data.get("newsletter_html")
        subject = data.get("newsletter_subject") or f"{NEWSLETTER_NAME} — {datetime.now().strftime('%B %d, %Y')}"
        print(f"[agent4]  Loaded newsletter_html from Firestore (run_id={data.get('run_id')})")
    else:
        html_path = os.path.join(DATA_DIR, "newsletter.html")
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        subject = f"{NEWSLETTER_NAME} — {datetime.now().strftime('%B %d, %Y')}"
        print(f"[agent4]  Loaded newsletter HTML from {html_path}")

    print("\n=== Sending email ===")
    if not RECIPIENT_EMAIL:
        print("  [warn]  NEWSLETTER_RECIPIENT_EMAIL not set — skipping send")
    else:
        api_key = _get_sendgrid_api_key()
        send_email(api_key, html, subject)

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n--- Done in {elapsed:.1f}s ---")


if __name__ == "__main__":
    run(run_id="local-debug")