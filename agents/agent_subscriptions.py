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
from datetime import datetime, timezone, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from auth_middleware import get_current_user

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

# Token TTLs: short window for confirmation, long-lived for action links
CONFIRM_TOKEN_TTL = timedelta(hours=48)
ACTION_TOKEN_TTL  = timedelta(days=365)

# Admin token for the /stats endpoint (no auth required if unset)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

# Comma-separated list of emails granted premium tier.
PREMIUM_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("PREMIUM_EMAILS", "").split(",")
    if e.strip()
}

DEFAULT_SECTIONS = [
    "Model & Product Releases",
    "Industry & Business",
    "Policy, Law & Regulation",
    "Open Source & Tools",
    "Safety & Alignment",
    "Society & Culture",
    "Research Spotlights",
]

limiter = Limiter(key_func=get_remote_address)
router  = APIRouter()


def _get_user_tier(email: str) -> str:
    return "premium" if email in PREMIUM_EMAILS else "free"


# ---------------------------------------------------------------------------
# Firestore helpers
# ---------------------------------------------------------------------------
# This agent always talks to Firestore — there is no local-file mode.
# For local testing, authenticate with:  gcloud auth application-default login

def _db():
    from google.cloud import firestore as _fs
    return _fs.Client(project=GCP_PROJECT_ID)


def _find_by_token(db, token: str):
    """Return the subscriber doc snapshot matching this token, or None.

    Returns None if the token is missing, not found, or expired.
    """
    if not token:
        return None
    docs = list(
        db.collection(SUBSCRIBERS_COLLECTION)
        .where("token", "==", token)
        .limit(1)
        .stream()
    )
    if not docs:
        return None
    doc  = docs[0]
    data = doc.to_dict()
    expires_at = data.get("token_expires_at")
    if expires_at:
        now = datetime.now(timezone.utc)
        if getattr(expires_at, "tzinfo", None) is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at:
            return None
    return doc


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
        print(f"[subscriptions]  email sent — status {resp.status}", flush=True)


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
        + f'<img src="https://newsletter.lofeodo.com/images/logo-email.png" alt="Latent SpaceMail"'
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

        # DOMINANT HEADLINE — 48px amber, Emigre display scale; also a link so mobile users see it as clickable
        f'<tr><td style="background:#0e0e0c;padding:44px 40px 0 32px;border-left:4px solid #c8b89a;">'
        f'<a href="{cta_link}" style="display:block;font-family:{_MONO};font-size:48px;font-weight:700;color:#c8b89a;letter-spacing:-3px;line-height:0.9;text-transform:uppercase;text-decoration:none;">{headline_big}</a>'
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
        f'<p style="font-family:{_MONO};font-size:10px;line-height:1.7;color:#9a9088;margin:0;">{footer_note}</p>'
        f'</td></tr>'

        # 1px amber bottom hairline
        f'<tr><td style="background:#c8b89a;height:1px;font-size:0;line-height:0;">&nbsp;</td></tr>'

        f'</table></td></tr></table></body></html>'
    )


def _confirm_email_html(token: str) -> str:
    link = f"{SERVICE_BASE_URL}/confirm?token={token}"
    body = (
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#9a9088;margin:0 0 16px 0;">'
        'Someone (hopefully you) asked to subscribe this address to '
        f'<span style="color:#c8b89a;font-weight:700;">Latent SpaceMail</span>, a weekly AI research &amp; news briefing.'
        '</p>'
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#9a9088;margin:0;">'
        'Tap <strong style="color:#c8b89a;">CONFIRM</strong> above or click the button below to start receiving your dispatch every Monday morning.'
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
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#9a9088;margin:0 0 16px 0;">'
        'Someone (hopefully you) asked to unsubscribe this address from '
        f'<span style="color:#c8b89a;font-weight:700;">Latent SpaceMail</span>.'
        '</p>'
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#9a9088;margin:0;">'
        'Tap <strong style="color:#c8b89a;">CONFIRM</strong> above or click the button below to complete your unsubscription. '
        "You won't receive any further emails after this."
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
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#9a9088;margin:0 0 16px 0;">'
        'You requested a link to update your '
        f'<span style="color:#c8b89a;font-weight:700;">Latent SpaceMail</span> subscription preferences.'
        '</p>'
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#9a9088;margin:0;">'
        'Tap <strong style="color:#c8b89a;">MANAGE</strong> above or click the button below to choose what content to include — French-language sources, Canadian AI coverage, and more.'
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
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#9a9088;margin:0 0 16px 0;">'
        'This address already has an active '
        f'<span style="color:#c8b89a;font-weight:700;">Latent SpaceMail</span> subscription — no action needed.'
        '</p>'
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#9a9088;margin:0;">'
        "If you'd like to update your content preferences — French-language sources, Canadian coverage — tap the heading above or click the button below."
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


def _verify_email_html(link: str) -> str:
    body = (
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#9a9088;margin:0 0 16px 0;">'
        'You created an account on '
        f'<span style="color:#c8b89a;font-weight:700;">Latent SpaceMail</span>. '
        'Click below to verify your email address and activate your account.'
        '</p>'
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#9a9088;margin:0;">'
        'Tap <strong style="color:#c8b89a;">VERIFY</strong> above or click the button below. '
        'This link expires in 24 hours.'
        '</p>'
    )
    return _email_shell(
        headline_big="VERIFY",
        headline_small="YOUR EMAIL",
        body_html=body,
        cta_text="VERIFY EMAIL ADDRESS",
        cta_link=link,
        footer_note="If you didn't create this account, you can safely ignore this email.",
        tx_code="VERIFY",
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
    <a href="{base}">Home</a>
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
    email: Annotated[str, Field(max_length=254)]
    prefs: Prefs = Prefs()
    send_latest: bool = False


class EmailOnlyRequest(BaseModel):
    email: Annotated[str, Field(max_length=254)]


class PreferencesUpdate(BaseModel):
    token: Annotated[str, Field(max_length=128)]
    prefs: Prefs


class AuthPreferencesUpdate(BaseModel):
    prefs: Prefs


class SectionRefineRequest(BaseModel):
    raw_topic: Annotated[str, Field(max_length=200)]


class CustomSectionItem(BaseModel):
    id:            Annotated[str, Field(max_length=64)]
    raw_input:     Annotated[str, Field(max_length=200)]
    refined_topic: Annotated[str, Field(max_length=200)]


class SectionConfig(BaseModel):
    enabled_sections: list[str] | None = None
    custom_sections:  list[CustomSectionItem] = []


class SectionConfigUpdate(BaseModel):
    section_config: SectionConfig


# ---------------------------------------------------------------------------
# Public stats endpoint
# ---------------------------------------------------------------------------

@router.get("/stats")
def get_stats(token: Annotated[str, Query(max_length=128)] = ""):
    if ADMIN_TOKEN and token != ADMIN_TOKEN:
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    db = _db()
    count = _active_subscriber_count(db)
    return {"active": count, "max": MAX_SUBSCRIBERS}


# ---------------------------------------------------------------------------
# Website-initiated endpoints (no token — always return 200)
# ---------------------------------------------------------------------------

@router.post("/subscribe")
@limiter.limit("5/minute")
def subscribe(request: Request, req: SubscribeRequest):
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
        raise HTTPException(status_code=503, detail="service_unavailable")

    # New subscriber, or inactive one re-subscribing: (re)generate the token.
    # Note: regenerating invalidates links in previously sent emails — fine.
    token      = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + CONFIRM_TOKEN_TTL
    ref.set({
        "email":            email,
        "subscribed_at":    datetime.now(timezone.utc),
        "confirmed_at":     None,
        "active":           False,        # double opt-in: activated by /confirm
        "token":            token,
        "token_expires_at": expires_at,
        "send_latest":      req.send_latest,
        "latest_sent":      False,
        "prefs": {
            "include_french": req.prefs.include_french,
            "include_canada": req.prefs.include_canada,
        },
    }, merge=True)

    _send_email(email, f"Confirm your subscription to {NEWSLETTER_NAME}",
                _confirm_email_html(token))
    return {"status": "ok"}


@router.get("/preview")
@limiter.limit("30/minute")
def newsletter_preview(request: Request):
    """Return the latest newsletter HTML for embedding in the preview iframe."""
    db = _db()
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
        return HTMLResponse("<p>No issue available yet. Check back Monday!</p>", status_code=404)

    data = docs[0].to_dict()
    variants = data.get("newsletter_variants") or {}
    # Show the Canada-inclusive variant so visitors see the full scope of the newsletter.
    # Fall back through 0_0 and then the legacy newsletter_html field.
    html = variants.get("0_1") or variants.get("0_0") or data.get("newsletter_html", "")

    subscribe_url = f"{FRONTEND_BASE_URL}/"
    html = html.replace("{{UNSUBSCRIBE_URL}}", subscribe_url)
    html = html.replace("{{PREFERENCES_URL}}", subscribe_url)

    # Normalize stale image base URLs from old pipeline runs to the canonical custom domain.
    html = html.replace(
        "https://latentspacemail.web.app/newsletter/images/",
        "https://newsletter.lofeodo.com/images/",
    )

    # Open article links in a new tab (external sites block iframe embedding).
    html = re.sub(
        r'(<a\b(?:(?!target=)[^>])*?)(href="https?://[^"]*")',
        r'\1\2 target="_blank" rel="noopener noreferrer"',
        html,
    )

    headers = {"Content-Security-Policy": "frame-ancestors https://latentspacemail.web.app https://latentspacemail.firebaseapp.com https://newsletter.lofeodo.com"}
    return HTMLResponse(content=html, status_code=200, headers=headers)


@router.post("/request-unsubscribe")
@limiter.limit("5/minute")
def request_unsubscribe(request: Request, req: EmailOnlyRequest):
    email = req.email.strip().lower()
    db  = _db()
    doc = db.collection(SUBSCRIBERS_COLLECTION).document(email).get()

    if doc.exists and doc.to_dict().get("active"):
        _send_email(email, f"Confirm unsubscription from {NEWSLETTER_NAME}",
                    _unsubscribe_email_html(doc.to_dict()["token"]))
    # Same response whether or not the subscriber exists.
    return {"status": "ok"}


@router.post("/request-preferences")
@limiter.limit("5/minute")
def request_preferences(request: Request, req: EmailOnlyRequest):
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
@limiter.limit("10/minute")
def confirm(
    request: Request,
    token: Annotated[str, Query(max_length=128)] = "",
):
    if not token or not re.match(r'^[A-Za-z0-9_-]+$', token):
        return _INVALID_LINK_PAGE

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

    # Rotate token to a long-lived action token on confirmation.
    new_token  = secrets.token_urlsafe(32)
    action_exp = datetime.now(timezone.utc) + ACTION_TOKEN_TTL
    doc.reference.update({
        "active":            True,
        "confirmed_at":      datetime.now(timezone.utc),   # CASL proof of consent
        "token":             new_token,
        "token_expires_at":  action_exp,
    })
    print("[subscriptions]  confirmed subscriber", flush=True)

    # Send last week's newsletter if they asked for it (once — guard against
    # double-clicks on the confirm link).
    if data.get("send_latest") and not data.get("latest_sent"):
        html = _latest_newsletter_html(db, unsubscribe_token=new_token, prefs=data.get("prefs", {}))
        if html:
            _send_email(data["email"],
                        f"{NEWSLETTER_NAME} — last week's edition",
                        html)
            doc.reference.update({"latest_sent": True})

    return _full_page("Subscription confirmed 🎉",
                 f"You'll receive {NEWSLETTER_NAME} every Monday morning.")


@router.get("/unsubscribe")
@limiter.limit("10/minute")
def unsubscribe(
    request: Request,
    token: Annotated[str, Query(max_length=128)] = "",
):
    if not token or not re.match(r'^[A-Za-z0-9_-]+$', token):
        return _INVALID_LINK_PAGE

    db  = _db()
    doc = _find_by_token(db, token)
    if doc is None:
        return _INVALID_LINK_PAGE

    doc.reference.update({"active": False})
    print("[subscriptions]  unsubscribed subscriber", flush=True)
    return _full_page("You've been unsubscribed",
                 "Sorry to see you go. You can re-subscribe any time.")


@router.get("/preferences")
@limiter.limit("10/minute")
def get_preferences(
    request: Request,
    token: Annotated[str, Query(max_length=128)] = "",
):
    if not token or not re.match(r'^[A-Za-z0-9_-]+$', token):
        return JSONResponse(status_code=404, content={"error": "not_found"})

    db  = _db()
    doc = _find_by_token(db, token)
    if doc is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})

    data = doc.to_dict()
    return {"email": data["email"], "prefs": data.get("prefs", {})}


@router.post("/preferences")
@limiter.limit("10/minute")
def update_preferences(request: Request, req: PreferencesUpdate):
    if not req.token or not re.match(r'^[A-Za-z0-9_-]+$', req.token):
        return JSONResponse(status_code=404, content={"error": "not_found"})

    db  = _db()
    doc = _find_by_token(db, req.token)
    if doc is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})

    doc.reference.update({"prefs": {
        "include_french": req.prefs.include_french,
        "include_canada": req.prefs.include_canada,
    }})
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Latest newsletter lookup (same composite-index query as agent4)
# ---------------------------------------------------------------------------

def _detect_provider(decoded_token: dict) -> str:
    provider = decoded_token.get("firebase", {}).get("sign_in_provider", "")
    if "google" in provider:
        return "google"
    return "password"


def _newsletter_variant_key(prefs: dict) -> str:
    fr = "1" if prefs.get("include_french", False) else "0"
    ca = "1" if prefs.get("include_canada", False) else "0"
    return f"{fr}_{ca}"


def _latest_newsletter_html(db, unsubscribe_token: str, prefs: dict | None = None):
    from google.cloud import firestore as _fs
    docs = list(
        db.collection(FIRESTORE_COLLECTION)
        .where("newsletter_composed", "==", True)
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


# ---------------------------------------------------------------------------
# Account-based endpoints (Firebase Auth JWT required)
# ---------------------------------------------------------------------------
# These routes accept an "Authorization: Bearer <firebase_id_token>" header
# and operate on the authenticated user's account instead of a shared token.
# All legacy token-based routes above remain fully functional.

USERS_COLLECTION = "users"


@router.get("/auth/me")
@limiter.limit("30/minute")
async def auth_me(request: Request, user: dict = Depends(get_current_user)):
    """Return the current user's profile and subscription status."""
    db    = _db()
    email = user.get("email", "").lower()
    uid   = user["uid"]
    now   = datetime.now(timezone.utc)
    tier  = _get_user_tier(email)

    db.collection(USERS_COLLECTION).document(uid).set({
        "email":        email,
        "display_name": user.get("name"),
        "provider":     _detect_provider(user),
        "created_at":   now,
        "tier":         tier,
    }, merge=True)

    sub_doc  = db.collection(SUBSCRIBERS_COLLECTION).document(email).get()
    sub_data = sub_doc.to_dict() if sub_doc.exists else None
    return {
        "uid":            uid,
        "email":          email,
        "display_name":   user.get("name"),
        "email_verified": user.get("email_verified", False),
        "subscribed":     bool(sub_data and sub_data.get("active")),
        "prefs":          sub_data.get("prefs", {}) if sub_data else {},
        "tier":           tier,
    }


@router.post("/auth/subscribe")
@limiter.limit("10/minute")
async def auth_subscribe(request: Request, user: dict = Depends(get_current_user)):
    """Subscribe the authenticated user. No confirmation email — account email is already verified."""
    if not user.get("email_verified"):
        raise HTTPException(status_code=403, detail="email_not_verified")

    try:
        body = await request.json()
    except Exception:
        body = {}
    send_latest = bool(body.get("send_latest", False))

    db    = _db()
    email = user["email"].lower()
    uid   = user["uid"]
    now   = datetime.now(timezone.utc)

    ref = db.collection(SUBSCRIBERS_COLLECTION).document(email)
    doc = ref.get()

    if doc.exists and doc.to_dict().get("active"):
        return {"status": "already_subscribed"}

    if _active_subscriber_count(db) >= MAX_SUBSCRIBERS:
        raise HTTPException(status_code=503, detail="service_unavailable")

    token_val = secrets.token_urlsafe(32)
    if doc.exists:
        ref.update({
            "active":       True,
            "confirmed_at": now,
            "uid":          uid,
            "send_latest":  send_latest,
            "latest_sent":  False,
        })
    else:
        ref.set({
            "email":            email,
            "subscribed_at":    now,
            "confirmed_at":     now,
            "active":           True,
            "token":            token_val,
            "token_expires_at": now + ACTION_TOKEN_TTL,
            "send_latest":      send_latest,
            "latest_sent":      False,
            "uid":              uid,
            "prefs": {"include_french": True, "include_canada": True},
        })

    if send_latest:
        current_data = ref.get().to_dict()
        html = _latest_newsletter_html(
            db,
            unsubscribe_token=current_data["token"],
            prefs=current_data.get("prefs", {}),
        )
        if html:
            _send_email(email, f"{NEWSLETTER_NAME} — last week's edition", html)
            ref.update({"latest_sent": True})

    print(f"[subscriptions]  account-based subscribe: {email}", flush=True)
    return {"status": "ok"}


@router.post("/auth/unsubscribe")
@limiter.limit("10/minute")
async def auth_unsubscribe(request: Request, user: dict = Depends(get_current_user)):
    """Unsubscribe the authenticated user."""
    db    = _db()
    email = user["email"].lower()
    ref   = db.collection(SUBSCRIBERS_COLLECTION).document(email)
    if ref.get().exists:
        ref.update({"active": False})
    print(f"[subscriptions]  account-based unsubscribe: {email}", flush=True)
    return {"status": "ok"}


@router.post("/auth/send-verification-email")
@limiter.limit("5/minute")
async def auth_send_verification_email(request: Request, user: dict = Depends(get_current_user)):
    """Send a themed email verification email for email+password accounts."""
    if user.get("email_verified"):
        return {"status": "already_verified"}

    email = user.get("email", "").lower()
    import firebase_admin
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
    from firebase_admin import auth as fb_auth
    try:
        link = fb_auth.generate_email_verification_link(email)
    except Exception as exc:
        print(f"[subscriptions]  generate_email_verification_link failed: {exc}", flush=True)
        raise HTTPException(status_code=503, detail="verification_link_failed")

    _send_email(email, f"Verify your {NEWSLETTER_NAME} account", _verify_email_html(link))
    print(f"[subscriptions]  verification email sent: {email}", flush=True)
    return {"status": "ok"}


@router.get("/auth/preferences")
@limiter.limit("10/minute")
async def auth_get_preferences(request: Request, user: dict = Depends(get_current_user)):
    """Return the authenticated user's current preferences and tier."""
    db    = _db()
    email = user["email"].lower()
    tier  = _get_user_tier(email)
    doc   = db.collection(SUBSCRIBERS_COLLECTION).document(email).get()
    if not doc.exists or not doc.to_dict().get("active"):
        return {"subscribed": False, "prefs": {}, "tier": tier}
    data = doc.to_dict()
    return {"subscribed": True, "prefs": data.get("prefs", {}), "tier": tier}


@router.post("/auth/preferences")
@limiter.limit("10/minute")
async def auth_update_preferences(
    request: Request,
    req: AuthPreferencesUpdate,
    user: dict = Depends(get_current_user),
):
    """Update the authenticated user's preferences."""
    db    = _db()
    email = user["email"].lower()
    ref   = db.collection(SUBSCRIBERS_COLLECTION).document(email)
    if not ref.get().exists:
        raise HTTPException(status_code=404, detail="not_subscribed")
    ref.update({"prefs": {
        "include_french": req.prefs.include_french,
        "include_canada": req.prefs.include_canada,
    }})
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Premium: custom section endpoints
# ---------------------------------------------------------------------------

@router.post("/auth/sections/refine")
@limiter.limit("5/minute")
async def auth_sections_refine(
    request: Request,
    req: SectionRefineRequest,
    user: dict = Depends(get_current_user),
):
    """Use Claude to refine a free-form topic into a clean newsletter section title."""
    email = user.get("email", "").lower()
    if _get_user_tier(email) != "premium":
        raise HTTPException(status_code=403, detail="premium_required")

    import anthropic as _anthropic
    client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_1ST_API_KEY", ""))
    raw = req.raw_topic.strip()
    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[{
                "role": "user",
                "content": (
                    f'A newsletter subscriber wants a custom section about: "{raw}"\n'
                    "Return only a concise newsletter section title (4–8 words) that clearly captures "
                    "what they want covered — e.g. \"SpaceX product launches & mission updates\". "
                    "No explanation, just the title."
                ),
            }],
        )
        refined = message.content[0].text.strip().strip("\"'")
    except Exception:
        raise HTTPException(status_code=503, detail="ai_unavailable")

    return {"refined_topic": refined}


@router.get("/auth/sections")
@limiter.limit("10/minute")
async def auth_get_sections(request: Request, user: dict = Depends(get_current_user)):
    """Return the user's section configuration (premium only)."""
    email = user.get("email", "").lower()
    if _get_user_tier(email) != "premium":
        raise HTTPException(status_code=403, detail="premium_required")

    uid      = user["uid"]
    db       = _db()
    user_doc = db.collection(USERS_COLLECTION).document(uid).get()
    section_config = user_doc.to_dict().get("section_config") if user_doc.exists else None
    return {"default_sections": DEFAULT_SECTIONS, "section_config": section_config}


@router.post("/auth/sections")
@limiter.limit("10/minute")
async def auth_update_sections(
    request: Request,
    req: SectionConfigUpdate,
    user: dict = Depends(get_current_user),
):
    """Save the user's section configuration (premium only)."""
    email = user.get("email", "").lower()
    if _get_user_tier(email) != "premium":
        raise HTTPException(status_code=403, detail="premium_required")

    if req.section_config.enabled_sections is not None:
        valid = set(DEFAULT_SECTIONS)
        for s in req.section_config.enabled_sections:
            if s not in valid:
                raise HTTPException(status_code=422, detail="unknown_section")

    uid = user["uid"]
    db  = _db()
    db.collection(USERS_COLLECTION).document(uid).update({
        "section_config": {
            "enabled_sections": req.section_config.enabled_sections,
            "custom_sections": [
                {"id": cs.id, "raw_input": cs.raw_input, "refined_topic": cs.refined_topic}
                for cs in req.section_config.custom_sections
            ],
        }
    })
    return {"status": "ok"}