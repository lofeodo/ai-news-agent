# agents/agent_subscriptions.py
#
# Subscription service for Latent SpaceMail.
#
# Unlike the pipeline agents, this module does NOT define run(run_id).
# It defines a FastAPI APIRouter that main.py mounts when
# AGENT_NAME == "agent_subscriptions". All work happens synchronously
# inside the request — a browser is waiting for the response.
#
# Auth model: "the inbox is the authentication."
#   - Website-initiated actions (subscribe / request-unsubscribe /
#     request-preferences) trust no one → they trigger an email round-trip.
#   - Token-carrying actions (confirm / unsubscribe / preferences) prove
#     inbox ownership, because the token only ever travels inside an email.
#
# Website-initiated endpoints ALWAYS return 200, whether or not the email
# exists — otherwise the form becomes an oracle for checking who's subscribed.

import os
import re
import secrets
import sys
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import GCP_PROJECT_ID, FIRESTORE_COLLECTION, SUBSCRIBERS_COLLECTION

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEWSLETTER_NAME      = "Latent SpaceMail"
SENDER_EMAIL         = "latentspacemail@gmail.com"   # Phase 12: newsletter@latentspacemail.com
SENDER_NAME          = "Latent SpaceMail"
SENDGRID_SECRET_NAME = "sendgrid-api-key"
USE_SECRET_MANAGER   = os.environ.get("USE_SECRET_MANAGER", "false").lower() == "true"

# Public base URL of THIS service — used to build /confirm and /unsubscribe
# links. Unknown until the first deploy; deploy once, copy the URL, set the
# env var, deploy again (chicken-and-egg, one-time only).
SERVICE_BASE_URL  = os.environ.get("SERVICE_BASE_URL", "").rstrip("/")

# Base URL of the Firebase Hosting frontend — used for the preferences
# magic link, which lands on a frontend page, not on this API.
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "").rstrip("/")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

router = APIRouter()


# ---------------------------------------------------------------------------
# Firestore helpers
# ---------------------------------------------------------------------------
# This agent always talks to Firestore — there is no local-file mode.
# For local testing, authenticate with:  gcloud auth application-default login

def _db():
    from google.cloud import firestore as _fs
    return _fs.Client(project=GCP_PROJECT_ID)


def _find_by_token(db, token: str):
    """Return the subscriber doc snapshot matching this token, or None."""
    if not token:
        return None
    docs = list(
        db.collection(SUBSCRIBERS_COLLECTION)
        .where("token", "==", token)
        .limit(1)
        .stream()
    )
    return docs[0] if docs else None


# ---------------------------------------------------------------------------
# SendGrid (same Secret Manager pattern as agent4)
# ---------------------------------------------------------------------------

def _get_sendgrid_api_key() -> str:
    if USE_SECRET_MANAGER:
        from google.cloud import secretmanager
        client   = secretmanager.SecretManagerServiceClient()
        name     = f"projects/{GCP_PROJECT_ID}/secrets/{SENDGRID_SECRET_NAME}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8").strip()
    key = os.environ.get("SENDGRID_API_KEY", "")
    if not key:
        raise RuntimeError("SENDGRID_API_KEY env var not set for local mode")
    return key


def _send_email(to_email: str, subject: str, html_body: str) -> None:
    import json
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
            "Authorization": f"Bearer {_get_sendgrid_api_key()}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        print(f"[subscriptions]  email sent to {to_email} — status {resp.status}", flush=True)


# ---------------------------------------------------------------------------
# Email bodies
# ---------------------------------------------------------------------------

def _confirm_email_html(token: str) -> str:
    link = f"{SERVICE_BASE_URL}/confirm?token={token}"
    return f"""
    <div style="font-family: Georgia, serif; max-width: 560px; margin: auto;">
      <h2>Confirm your subscription to {NEWSLETTER_NAME}</h2>
      <p>Someone (hopefully you) asked to subscribe this address to
         {NEWSLETTER_NAME}, a weekly AI research &amp; news briefing.</p>
      <p><a href="{link}">Click here to confirm your subscription</a></p>
      <p style="color:#777; font-size: 13px;">If this wasn't you, simply
         ignore this email — you will not be subscribed.</p>
    </div>
    """


def _unsubscribe_email_html(token: str) -> str:
    link = f"{SERVICE_BASE_URL}/unsubscribe?token={token}"
    return f"""
    <div style="font-family: Georgia, serif; max-width: 560px; margin: auto;">
      <h2>Unsubscribe from {NEWSLETTER_NAME}</h2>
      <p>Someone (hopefully you) asked to unsubscribe this address.</p>
      <p><a href="{link}">Click here to confirm and unsubscribe</a></p>
      <p style="color:#777; font-size: 13px;">If this wasn't you, ignore this
         email — your subscription is unchanged.</p>
    </div>
    """


def _preferences_email_html(token: str) -> str:
    link = f"{FRONTEND_BASE_URL}/preferences.html?token={token}"
    return f"""
    <div style="font-family: Georgia, serif; max-width: 560px; margin: auto;">
      <h2>Manage your {NEWSLETTER_NAME} preferences</h2>
      <p><a href="{link}">Click here to update your preferences</a></p>
      <p style="color:#777; font-size: 13px;">This link is unique to you —
         don't share it. If you didn't request this, you can ignore it.</p>
    </div>
    """


def _already_subscribed_email_html(token: str) -> str:
    link = f"{FRONTEND_BASE_URL}/preferences.html?token={token}"
    return f"""
    <div style="font-family: Georgia, serif; max-width: 560px; margin: auto;">
      <h2>You're already subscribed to {NEWSLETTER_NAME}</h2>
      <p>This address already has an active subscription. If you wanted to
         change your preferences, <a href="{link}">click here</a>.</p>
    </div>
    """


# ---------------------------------------------------------------------------
# Small HTML pages returned by the GET endpoints (clicked from emails)
# ---------------------------------------------------------------------------

def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(status_code=status, content=f"""
    <html><body style="font-family: Georgia, serif; max-width: 560px;
                       margin: 80px auto; text-align: center;">
      <h2>{title}</h2><p>{body}</p>
    </body></html>
    """)


_INVALID_LINK_PAGE = _page(
    "Invalid or expired link",
    "This link is no longer valid. You can request a fresh one from the website.",
    status=404,
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class Prefs(BaseModel):
    include_french: bool = False
    include_canada: bool = False


class SubscribeRequest(BaseModel):
    email: str
    prefs: Prefs = Prefs()
    send_latest: bool = False


class EmailOnlyRequest(BaseModel):
    email: str


class PreferencesUpdate(BaseModel):
    token: str
    prefs: Prefs


# ---------------------------------------------------------------------------
# Website-initiated endpoints (no token — always return 200)
# ---------------------------------------------------------------------------

@router.post("/subscribe")
def subscribe(req: SubscribeRequest):
    email = req.email.strip().lower()
    if not EMAIL_RE.match(email):
        # The one case where we don't pretend success: a malformed address
        # is a UX problem (typo), not an information leak.
        return JSONResponse(status_code=422, content={"error": "invalid email address"})

    db  = _db()
    ref = db.collection(SUBSCRIBERS_COLLECTION).document(email)
    doc = ref.get()

    if doc.exists and doc.to_dict().get("active"):
        # Already active — tell them via email (NOT via the HTTP response,
        # which stays identical to the new-subscriber case).
        _send_email(email, f"You're already subscribed to {NEWSLETTER_NAME}",
                    _already_subscribed_email_html(doc.to_dict()["token"]))
        return {"status": "ok"}

    # New subscriber, or inactive one re-subscribing: (re)generate the token.
    # Note: regenerating invalidates links in previously sent emails — fine.
    token = secrets.token_urlsafe(32)
    ref.set({
        "email":         email,
        "subscribed_at": datetime.now(timezone.utc),
        "confirmed_at":  None,
        "active":        False,           # double opt-in: activated by /confirm
        "token":         token,
        "send_latest":   req.send_latest,
        "latest_sent":   False,
        "prefs": {
            "include_french": req.prefs.include_french,
            "include_canada": req.prefs.include_canada,
        },
    }, merge=True)

    _send_email(email, f"Confirm your subscription to {NEWSLETTER_NAME}",
                _confirm_email_html(token))
    return {"status": "ok"}


@router.post("/request-unsubscribe")
def request_unsubscribe(req: EmailOnlyRequest):
    email = req.email.strip().lower()
    db  = _db()
    doc = db.collection(SUBSCRIBERS_COLLECTION).document(email).get()

    if doc.exists and doc.to_dict().get("active"):
        _send_email(email, f"Confirm unsubscription from {NEWSLETTER_NAME}",
                    _unsubscribe_email_html(doc.to_dict()["token"]))
    # Same response whether or not the subscriber exists.
    return {"status": "ok"}


@router.post("/request-preferences")
def request_preferences(req: EmailOnlyRequest):
    email = req.email.strip().lower()
    db  = _db()
    doc = db.collection(SUBSCRIBERS_COLLECTION).document(email).get()

    if doc.exists and doc.to_dict().get("active"):
        _send_email(email, f"Manage your {NEWSLETTER_NAME} preferences",
                    _preferences_email_html(doc.to_dict()["token"]))
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Token-carrying endpoints (clicked from inside an email)
# ---------------------------------------------------------------------------

@router.get("/confirm")
def confirm(token: str = ""):
    db  = _db()
    doc = _find_by_token(db, token)
    if doc is None:
        return _INVALID_LINK_PAGE

    data = doc.to_dict()
    doc.reference.update({
        "active":       True,
        "confirmed_at": datetime.now(timezone.utc),   # CASL proof of consent
    })
    print(f"[subscriptions]  confirmed: {data['email']}", flush=True)

    # Send last week's newsletter if they asked for it (once — guard against
    # double-clicks on the confirm link).
    if data.get("send_latest") and not data.get("latest_sent"):
        html = _latest_newsletter_html(db, unsubscribe_token=data["token"])
        if html:
            _send_email(data["email"],
                        f"{NEWSLETTER_NAME} — last week's edition",
                        html)
            doc.reference.update({"latest_sent": True})

    return _page("Subscription confirmed 🎉",
                 f"You'll receive {NEWSLETTER_NAME} every Monday morning.")


@router.get("/unsubscribe")
def unsubscribe(token: str = ""):
    db  = _db()
    doc = _find_by_token(db, token)
    if doc is None:
        return _INVALID_LINK_PAGE

    doc.reference.update({"active": False})
    print(f"[subscriptions]  unsubscribed: {doc.to_dict()['email']}", flush=True)
    return _page("You've been unsubscribed",
                 "Sorry to see you go. You can re-subscribe any time.")


@router.get("/preferences")
def get_preferences(token: str = ""):
    db  = _db()
    doc = _find_by_token(db, token)
    if doc is None:
        return JSONResponse(status_code=404, content={"error": "invalid token"})

    data = doc.to_dict()
    return {"email": data["email"], "prefs": data.get("prefs", {})}


@router.post("/preferences")
def update_preferences(req: PreferencesUpdate):
    db  = _db()
    doc = _find_by_token(db, req.token)
    if doc is None:
        return JSONResponse(status_code=404, content={"error": "invalid token"})

    doc.reference.update({"prefs": {
        "include_french": req.prefs.include_french,
        "include_canada": req.prefs.include_canada,
    }})
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Latest newsletter lookup (same composite-index query as agent4)
# ---------------------------------------------------------------------------

def _latest_newsletter_html(db, unsubscribe_token: str):
    from google.cloud import firestore as _fs
    docs = list(
        db.collection(FIRESTORE_COLLECTION)
        .where("newsletter_html", "!=", None)
        .order_by("newsletter_html")
        .order_by("started_at", direction=_fs.Query.DESCENDING)
        .limit(1)
        .stream()
    )
    if not docs:
        print("[subscriptions]  no newsletter found to send", flush=True)
        return None

    html = docs[0].to_dict().get("newsletter_html", "")
    # agent3 embeds two placeholders in the footer. Substitute both with this
    # subscriber's token-carrying links. NOTE: the preferences link depends on
    # FRONTEND_BASE_URL, which is unset until the frontend exists (Phase 11) —
    # until then the prefs link is malformed, but we still replace the
    # placeholder so the raw "{{PREFERENCES_URL}}" string never ships.
    unsub_link = f"{SERVICE_BASE_URL}/unsubscribe?token={unsubscribe_token}"
    prefs_link = f"{FRONTEND_BASE_URL}/preferences.html?token={unsubscribe_token}"
    html = html.replace("{{UNSUBSCRIBE_URL}}", unsub_link)
    html = html.replace("{{PREFERENCES_URL}}", prefs_link)
    return html