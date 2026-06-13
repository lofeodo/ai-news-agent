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

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import GCP_PROJECT_ID, FIRESTORE_COLLECTION, SUBSCRIBERS_COLLECTION, MAX_SUBSCRIBERS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEWSLETTER_NAME      = "Latent SpaceMail"
SENDER_EMAIL         = "newsletter@lofeodo.com"
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


def _active_subscriber_count(db) -> int:
    col = db.collection(SUBSCRIBERS_COLLECTION)
    try:
        result = col.where("active", "==", True).count().get()
        return result[0][0].value
    except Exception:
        return sum(1 for _ in col.where("active", "==", True).stream())


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

_MONO = "'Menlo','Cascadia Mono','Consolas','Courier New',monospace"


def _email_shell(
    headline_big: str,
    headline_small: str,
    body_html: str,
    cta_text: str,
    cta_link: str,
    footer_note: str,
    tx_code: str,
) -> str:
    """Emigre/zine-inspired shell: large stacked headline, amber left stripe, full-width CTA band."""
    return (
        f'<!DOCTYPE html><html lang="en"><head>'
        f'<meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        f'<meta name="color-scheme" content="light">'
        f'<meta name="format-detection" content="telephone=no,address=no,email=no">'
        f'</head>'
        f'<body style="margin:0;padding:0;font-family:{_MONO};">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td align="center" style="padding:44px 12px;">'
        f'<table width="560" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;width:100%;">'

        # 4px amber top bar
        f'<tr><td style="background:#c8b89a;height:4px;font-size:0;line-height:0;">&nbsp;</td></tr>'

        # HEADER: >_ | wordmark | right tx label — amber left stripe runs full height
        f'<tr><td style="background:#080808;padding:0;border-left:4px solid #c8b89a;">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="width:72px;padding:24px 0 24px 32px;vertical-align:middle;white-space:nowrap;">'
        f'<div style="font-family:{_MONO};font-size:40px;font-weight:700;color:#c8b89a;line-height:1;letter-spacing:-3px;">&#10095;_</div>'
        f'</td>'
        f'<td style="width:1px;padding:18px 0;background:#1e1e1e;">&nbsp;</td>'
        f'<td style="padding:22px 0 22px 20px;vertical-align:middle;">'
        + f'<img src="https://latentspacemail.web.app/images/logo-email.png" alt="Latent SpaceMail"'
          f' width="32" height="32" style="display:block;border:0;margin-bottom:8px;">'
        + f'<div style="font-family:{_MONO};font-size:16px;font-weight:700;color:#c8b89a;letter-spacing:0.5px;line-height:1.1;">LATENT SPACEMAIL</div>'
        f'<div style="font-family:{_MONO};font-size:8px;color:#2d2820;letter-spacing:3px;text-transform:uppercase;margin-top:9px;">Weekly AI Intelligence Dispatch</div>'
        f'</td>'
        f'<td style="padding:22px 32px 22px 0;vertical-align:bottom;text-align:right;white-space:nowrap;">'
        f'<div style="font-family:{_MONO};font-size:8px;color:#272318;letter-spacing:2px;text-transform:uppercase;">T&thinsp;&mdash;&thinsp;{tx_code}</div>'
        f'</td>'
        f'</tr></table></td></tr>'

        # Hairline separator
        f'<tr><td style="background:#141410;height:1px;font-size:0;line-height:0;">&nbsp;</td></tr>'

        # DOMINANT HEADLINE — 48px amber, Emigre display scale
        f'<tr><td style="background:#0e0e0c;padding:44px 40px 0 32px;border-left:4px solid #c8b89a;">'
        f'<div style="font-family:{_MONO};font-size:48px;font-weight:700;color:#c8b89a;letter-spacing:-3px;line-height:0.9;text-transform:uppercase;">{headline_big}</div>'
        f'</td></tr>'

        # Short amber rule — Weingart rhythm break
        f'<tr><td style="background:#0e0e0c;padding:18px 40px 16px 32px;border-left:4px solid #c8b89a;">'
        f'<table cellpadding="0" cellspacing="0" border="0"><tr><td style="background:#c8b89a;width:56px;height:3px;font-size:0;line-height:0;">&nbsp;</td></tr></table>'
        f'</td></tr>'

        # SECONDARY HEADLINE — tracked small caps
        f'<tr><td style="background:#0e0e0c;padding:0 40px 0 32px;border-left:4px solid #c8b89a;">'
        f'<div style="font-family:{_MONO};font-size:12px;font-weight:700;color:#6a6058;letter-spacing:5px;text-transform:uppercase;">{headline_small}</div>'
        f'</td></tr>'

        # BODY TEXT
        f'<tr><td style="background:#0e0e0c;padding:32px 40px 44px 32px;border-left:4px solid #c8b89a;">'
        f'{body_html}'
        f'</td></tr>'

        # Zone-break hairline + FULL-WIDTH CTA BAND
        f'<tr><td style="background:#c8b89a;height:1px;font-size:0;line-height:0;">&nbsp;</td></tr>'
        f'<tr><td style="background:#c8b89a;padding:0;border-left:4px solid #a89070;">'
        f'<a href="{cta_link}" style="display:block;font-family:{_MONO};font-size:11px;font-weight:700;color:#0e0e0c;text-decoration:none;letter-spacing:3.5px;text-transform:uppercase;padding:21px 32px;">'
        f'{cta_text} &nbsp;&nbsp; &#10095;'
        f'</a></td></tr>'

        # FOOTER — amber stripe continues
        f'<tr><td style="background:#080806;padding:15px 40px 18px 32px;border-left:4px solid #c8b89a;">'
        f'<p style="font-family:{_MONO};font-size:10px;line-height:1.7;color:#3e3830;margin:0;">{footer_note}</p>'
        f'</td></tr>'

        # 1px amber bottom hairline
        f'<tr><td style="background:#c8b89a;height:1px;font-size:0;line-height:0;">&nbsp;</td></tr>'

        f'</table></td></tr></table></body></html>'
    )


def _confirm_email_html(token: str) -> str:
    link = f"{SERVICE_BASE_URL}/confirm?token={token}"
    body = (
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#545048;margin:0 0 16px 0;">'
        'Someone (hopefully you) asked to subscribe this address to '
        f'<span style="color:#8a7e70;font-weight:700;">Latent SpaceMail</span>, a weekly AI research &amp; news briefing.'
        '</p>'
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#545048;margin:0;">'
        'Click the band below to confirm and start receiving your dispatch every Monday morning.'
        '</p>'
    )
    return _email_shell(
        headline_big="CONFIRM",
        headline_small="YOUR SUBSCRIPTION",
        body_html=body,
        cta_text="CONFIRM SUBSCRIPTION",
        cta_link=link,
        footer_note="If this wasn't you, simply ignore this email — you will not be subscribed.",
        tx_code="CONFIRM",
    )


def _unsubscribe_email_html(token: str) -> str:
    link = f"{SERVICE_BASE_URL}/unsubscribe?token={token}"
    body = (
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#545048;margin:0 0 16px 0;">'
        'Someone (hopefully you) asked to unsubscribe this address from '
        f'<span style="color:#8a7e70;font-weight:700;">Latent SpaceMail</span>.'
        '</p>'
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#545048;margin:0;">'
        "Click the band below to confirm. You won't receive any further emails after this."
        '</p>'
    )
    return _email_shell(
        headline_big="CONFIRM",
        headline_small="UNSUBSCRIBE",
        body_html=body,
        cta_text="CONFIRM UNSUBSCRIBE",
        cta_link=link,
        footer_note="If this wasn't you, ignore this email — your subscription is unchanged.",
        tx_code="UNSUB",
    )


def _preferences_email_html(token: str) -> str:
    link = f"{FRONTEND_BASE_URL}/preferences.html?token={token}"
    body = (
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#545048;margin:0 0 16px 0;">'
        'You requested a link to update your '
        f'<span style="color:#8a7e70;font-weight:700;">Latent SpaceMail</span> subscription preferences.'
        '</p>'
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#545048;margin:0;">'
        'Click below to choose what content to include — French-language sources, Canadian AI coverage, and more.'
        '</p>'
    )
    return _email_shell(
        headline_big="MANAGE",
        headline_small="YOUR PREFERENCES",
        body_html=body,
        cta_text="UPDATE PREFERENCES",
        cta_link=link,
        footer_note="This link is unique to you — don't share it. If you didn't request this, you can safely ignore it.",
        tx_code="PREFS",
    )


def _already_subscribed_email_html(token: str) -> str:
    link = f"{FRONTEND_BASE_URL}/preferences.html?token={token}"
    body = (
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#545048;margin:0 0 16px 0;">'
        'This address already has an active '
        f'<span style="color:#8a7e70;font-weight:700;">Latent SpaceMail</span> subscription — no action needed.'
        '</p>'
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#545048;margin:0;">'
        "If you'd like to update your content preferences — French-language sources, Canadian coverage — click below."
        '</p>'
    )
    return _email_shell(
        headline_big="ALREADY",
        headline_small="SUBSCRIBED",
        body_html=body,
        cta_text="MANAGE PREFERENCES",
        cta_link=link,
        footer_note="Didn't try to subscribe? No worries — nothing has changed on your account.",
        tx_code="ACTIVE",
    )


# ---------------------------------------------------------------------------
# Small HTML pages returned by the GET endpoints (clicked from emails)
# ---------------------------------------------------------------------------

def _full_page(title: str, body: str, status: int = 200) -> HTMLResponse:
    """Full-fidelity page matching the website aesthetic — canvas, logo, card, fonts."""
    base = FRONTEND_BASE_URL or ""
    return HTMLResponse(status_code=status, content=f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Latent SpaceMail — {title}</title>
  <link rel="preload" href="{base}/fonts/cormorant-garamond-600.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="{base}/fonts.css">
  <link rel="stylesheet" href="{base}/style.css">
</head>
<body>
  <canvas id="bg-canvas" style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:0;pointer-events:none;will-change:transform;"></canvas>
  <a class="wordmark" href="{base}" aria-label="Latent SpaceMail home">
    <img src="{base}/images/logo-mark.svg" alt="Latent SpaceMail" height="36" width="36">
  </a>
  <div class="card">
    <h1><span class="prompt">&#10095;</span> {title}</h1>
    <p class="subtitle">{body}</p>
  </div>
  <div class="nav-links">
    <a href="{base}">Subscribe</a>
  </div>
  <script src="{base}/bg.js"></script>
</body>
</html>""")


_INVALID_LINK_PAGE = _full_page(
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
# Public stats endpoint
# ---------------------------------------------------------------------------

@router.get("/stats")
def get_stats():
    db = _db()
    count = _active_subscriber_count(db)
    return {"active": count, "max": MAX_SUBSCRIBERS}


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

    # Enforce subscriber cap before accepting a new subscription.
    if _active_subscriber_count(db) >= MAX_SUBSCRIBERS:
        raise HTTPException(status_code=503, detail="subscriber_limit_reached")

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

    # Guard against the race condition where two people hold confirmation
    # links for the last open spot and both click at the same time.
    if not data.get("active", False) and _active_subscriber_count(db) >= MAX_SUBSCRIBERS:
        return _full_page(
            "Newsletter is full",
            "All spots were claimed just before you confirmed. Check back later.",
            status=503,
        )

    doc.reference.update({
        "active":       True,
        "confirmed_at": datetime.now(timezone.utc),   # CASL proof of consent
    })
    print(f"[subscriptions]  confirmed: {data['email']}", flush=True)

    # Send last week's newsletter if they asked for it (once — guard against
    # double-clicks on the confirm link).
    if data.get("send_latest") and not data.get("latest_sent"):
        html = _latest_newsletter_html(db, unsubscribe_token=data["token"], prefs=data.get("prefs", {}))
        if html:
            _send_email(data["email"],
                        f"{NEWSLETTER_NAME} — last week's edition",
                        html)
            doc.reference.update({"latest_sent": True})

    return _full_page("Subscription confirmed 🎉",
                 f"You'll receive {NEWSLETTER_NAME} every Monday morning.")


@router.get("/unsubscribe")
def unsubscribe(token: str = ""):
    db  = _db()
    doc = _find_by_token(db, token)
    if doc is None:
        return _INVALID_LINK_PAGE

    doc.reference.update({"active": False})
    print(f"[subscriptions]  unsubscribed: {doc.to_dict()['email']}", flush=True)
    return _full_page("You've been unsubscribed",
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

def _newsletter_variant_key(prefs: dict) -> str:
    fr = "1" if prefs.get("include_french", False) else "0"
    ca = "1" if prefs.get("include_canada", False) else "0"
    return f"{fr}_{ca}"


def _latest_newsletter_html(db, unsubscribe_token: str, prefs: dict | None = None):
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

    data     = docs[0].to_dict()
    variants = data.get("newsletter_variants")
    if variants and prefs:
        key  = _newsletter_variant_key(prefs)
        html = variants.get(key) or variants.get("0_0", "")
    else:
        html = data.get("newsletter_html", "")

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