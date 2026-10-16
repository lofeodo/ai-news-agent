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

import functools
import html as html_lib
import hmac
import logging
import os
import re
import secrets
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Annotated
from urllib.parse import urlencode

import anthropic
import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

from auth_middleware import get_current_user, get_optional_user, get_verified_user
import click_counts
import ids
import launch
import mail_throttle
import run_kind
import sendgrid_webhook
import subscriptions_model
import topics
from prompt_guard import GUARD_TOPIC_REFINE, neutralize_tags

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

# A sign-up that is never confirmed is left to a Firestore TTL rule on this field (see docs/runbook.md).
UNCONFIRMED_TTL = timedelta(days=7)

EMAIL_RE = ids.EMAIL_RE

# Token TTLs: short window for confirmation, long-lived for action links
CONFIRM_TOKEN_TTL = timedelta(hours=48)
ACTION_TOKEN_TTL  = timedelta(days=365)

SENDGRID_TIMEOUT_SECONDS = float(os.environ.get("SENDGRID_TIMEOUT_SECONDS", "8"))
REFINE_TIMEOUT_SECONDS   = 15
RESPONSE_FLOOR_SECONDS   = float(os.environ.get("RESPONSE_FLOOR_SECONDS", "2.0"))

# Admin token for the /stats endpoint (the endpoint is off while unset)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

# Comma-separated list of emails granted premium tier.
PREMIUM_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("PREMIUM_EMAILS", "").split(",")
    if e.strip()
}

# Comma-separated list of emails that may view unpublished (debug) runs through /auth/admin/issues.
ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", "").split(",")
    if e.strip()
}

# Google Sign-In (server-side OAuth Authorization Code flow — see CLAUDE.md).
# Client ID is not secret (it's exposed in the browser redirect URL); the
# secret follows the same USE_SECRET_MANAGER pattern as SendGrid/Anthropic.
GOOGLE_OAUTH_CLIENT_ID   = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_SECRET_NAME = "google-oauth-client-secret"

OAUTH_STATE_TTL         = timedelta(minutes=10)
OAUTH_EXCHANGE_CODE_TTL = timedelta(seconds=60)

OAUTH_STATES_COLLECTION         = "oauth_states"
OAUTH_EXCHANGE_CODES_COLLECTION = "oauth_exchange_codes"

# return_to is attacker-visible/craftable (it round-trips through the browser
# and Google's redirect) — allowlist known frontend pages rather than trying
# to validate arbitrary relative-URL syntax, which is an easy place to get an
# open redirect wrong.
ALLOWED_RETURN_PAGES = {"index.html", "preferences.html", "sections.html", "issues.html", "preview.html", "share.html"}
DEFAULT_RETURN_PAGE  = "index.html"

DEFAULT_SECTIONS = [
    "Model & Product Releases",
    "Policy, Law & Regulation",
    "Open Source & Tools",
    "Safety & Alignment",
    "Industry & Business",
    "Society & Culture",
    "Research Spotlights",
]

_KEY_LOG_BUDGET = 20


def _client_key(request: Request) -> str:
    """Address the rate limiter counts against.

    On Cloud Run the TCP peer is Google's front end, so get_remote_address would put every caller in one
    bucket. The front end appends the real caller to X-Forwarded-For; entries before it are client-supplied
    and untrusted, so only the last one is used. Anywhere else (local runs, tests) the peer address is used.
    """
    global _KEY_LOG_BUDGET
    peer = get_remote_address(request)
    key = peer
    hops = 0
    if os.environ.get("K_SERVICE"):
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        hops = len(parts)
        if parts:
            key = parts[-1]
    if _KEY_LOG_BUDGET > 0:
        _KEY_LOG_BUDGET -= 1
        print(f"[subscriptions]  limiter key: forwarded hops={hops}, same as peer={key == peer}", flush=True)
    return key


limiter = Limiter(key_func=_client_key)
router  = APIRouter()


def _is_admin(user: dict) -> bool:
    return bool(user.get("email_verified")) and user.get("email", "").lower() in ADMIN_EMAILS


def _get_user_tier(user: dict) -> str:
    email = (user.get("email") or "").lower()
    return "premium" if user.get("email_verified") and email in PREMIUM_EMAILS else "free"


# ---------------------------------------------------------------------------
# Firestore helpers
# ---------------------------------------------------------------------------
# This agent always talks to Firestore — there is no local-file mode.
# For local testing, authenticate with:  gcloud auth application-default login

def _db():
    from google.cloud import firestore as _fs
    if not GCP_PROJECT_ID:
        # google-cloud-firestore only guards against project=None, not "" --
        # an empty string is silently accepted and only fails (or silently
        # misdirects, if some other valid-but-wrong project id ends up here)
        # on the first real RPC. Every route in this file calls _db() before
        # touching Firestore, so failing here turns a misconfigured deploy
        # into an immediate, obvious error instead of every /auth/* call
        # quietly doing nothing.
        raise RuntimeError("GCP_PROJECT_ID is not set; refusing to create a Firestore client")
    return _fs.Client(project=GCP_PROJECT_ID)


def _find_by_token(db, token: str, enforce_expiry: bool = False):
    """Return the subscriber doc snapshot matching this token, or None.

    Returns None if the token is missing or not found. An expiry date only
    counts for pending subscribers (subscribed to no newsletter) or when
    enforce_expiry is set: the links in sent newsletters keep working for
    anyone still subscribed to one.
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
    if expires_at and (enforce_expiry or not subscriptions_model.subscribed_ids(data)):
        now = datetime.now(timezone.utc)
        if getattr(expires_at, "tzinfo", None) is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at:
            return None
    return doc


def _count(query) -> int:
    try:
        return query.count().get()[0][0].value
    except Exception:
        return sum(1 for _ in query.stream())


def _active_subscriber_count(db) -> int:
    """People subscribed to at least one newsletter: MAX_SUBSCRIBERS counts people, not subscriptions.

    AI subscribers (`active`) plus everyone else who gets some newsletter (`subscribed_any`, kept by
    subscriptions_model). Two equality queries, so no composite index is needed.
    """
    col = db.collection(SUBSCRIBERS_COLLECTION)
    return (_count(col.where("active", "==", True))
            + _count(col.where("active", "==", False).where("subscribed_any", "==", True)))


AI = subscriptions_model.AI


def _newsletter_name(newsletter_id: str) -> str:
    return topics.get_topic(newsletter_id).name if topics.is_valid_topic(newsletter_id) else NEWSLETTER_NAME


def _newsletter_names(newsletter_ids) -> str:
    """Names joined for a sentence: one name, "A and B", or "A, B and C"."""
    names = [_newsletter_name(i) for i in newsletter_ids] or [NEWSLETTER_NAME]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _check_newsletters(raw, admin: bool) -> list[str]:
    """Validated newsletter ids from a request, in site order; 404 for an unknown or hidden one (same answer for
    both, so the list of hidden topics is not probed). None or empty means the AI newsletter."""
    wanted = [str(i).strip().lower() for i in (raw or [AI])]
    visible = launch.visible_topic_ids(admin)
    if any(i not in visible for i in wanted):
        raise HTTPException(status_code=404, detail="unknown_newsletter")
    return [i for i in visible if i in wanted]


def _link_query(token: str, newsletter_id: str = AI) -> str:
    """Query string for an emailed unsubscribe or preferences link. No `n` means the AI newsletter, so the AI
    links (and every link already sent) are unchanged."""
    return urlencode({"token": token} if newsletter_id == AI else {"token": token, "n": newsletter_id})


def _claim_latest(db, ref) -> bool:
    """Atomically mark the "last week's edition" mail as taken; True for exactly one caller."""
    from google.cloud import firestore as _fs

    @_fs.transactional
    def _txn(transaction):
        data = ref.get(transaction=transaction).to_dict() or {}
        if data.get("latest_sent"):
            return False
        transaction.update(ref, {"latest_sent": True})
        return True

    return _txn(db.transaction())


def _consume_once(db, collection: str, doc_id: str) -> dict | None:
    """Atomically read-and-delete a single-use doc. None if missing/expired.

    Read-then-delete as two separate calls would let two concurrent requests
    both read before either deletes, redeeming the same code twice — this
    matters here (unlike _find_by_token above, which is intentionally
    multi-use) because OAuth state/exchange codes must be single-use.
    """
    from google.cloud import firestore as _fs

    ref = db.collection(collection).document(doc_id)

    @_fs.transactional
    def _txn(transaction):
        snapshot = ref.get(transaction=transaction)
        if not snapshot.exists:
            return None
        data = snapshot.to_dict()
        transaction.delete(ref)
        return data

    data = _txn(db.transaction())
    if data is None:
        return None
    expires_at = data.get("expires_at")
    if getattr(expires_at, "tzinfo", None) is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return data if datetime.now(timezone.utc) <= expires_at else None


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


ANTHROPIC_SECRET_NAME = "anthropic-api-key"


def _get_anthropic_api_key() -> str:
    if USE_SECRET_MANAGER:
        from google.cloud import secretmanager
        client   = secretmanager.SecretManagerServiceClient()
        name     = f"projects/{GCP_PROJECT_ID}/secrets/{ANTHROPIC_SECRET_NAME}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8").strip()
    key = os.environ.get("ANTHROPIC_1ST_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_1ST_API_KEY env var not set")
    return key


def _get_google_oauth_client_secret() -> str:
    if USE_SECRET_MANAGER:
        from google.cloud import secretmanager
        client   = secretmanager.SecretManagerServiceClient()
        name     = f"projects/{GCP_PROJECT_ID}/secrets/{GOOGLE_OAUTH_SECRET_NAME}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8").strip()
    secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    if not secret:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_SECRET env var not set for local mode")
    return secret


SENDGRID_WEBHOOK_KEY_SECRET_NAME = "sendgrid-webhook-public-key"


def _get_sendgrid_webhook_public_key() -> str:
    """Base64 public key from the SendGrid console (Event Webhook > Signature Verification)."""
    if USE_SECRET_MANAGER:
        from google.cloud import secretmanager
        client   = secretmanager.SecretManagerServiceClient()
        name     = f"projects/{GCP_PROJECT_ID}/secrets/{SENDGRID_WEBHOOK_KEY_SECRET_NAME}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8").strip()
    key = os.environ.get("SENDGRID_WEBHOOK_PUBLIC_KEY", "")
    if not key:
        raise RuntimeError("SENDGRID_WEBHOOK_PUBLIC_KEY env var not set for local mode")
    return key


def _hold_until(start: float, floor: float) -> None:
    remaining = floor - (time.monotonic() - start)
    if remaining > 0:
        time.sleep(remaining)


def steady_response_time(fn):
    """Take at least RESPONSE_FLOOR_SECONDS, so the time a mail route takes does not depend on the address.

    Slept inside the request on purpose: the service gets no CPU between requests, so a background send could stall.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        start = time.monotonic()
        try:
            return fn(*args, **kwargs)
        finally:
            _hold_until(start, RESPONSE_FLOOR_SECONDS)
    return wrapper


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
    with urllib.request.urlopen(req, timeout=SENDGRID_TIMEOUT_SECONDS) as resp:
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


def _newsletter_list_html(newsletter_ids) -> str:
    """The newsletters named in a confirmation mail: bold names with their taglines."""
    items = []
    for i in newsletter_ids:
        t = topics.get_topic(i)
        items.append(f'<span style="color:#c8b89a;font-weight:700;">{html_lib.escape(t.name)}</span>'
                     f' ({html_lib.escape(t.tagline)})')
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _confirm_email_html(token: str, newsletter_ids=None) -> str:
    link = f"{SERVICE_BASE_URL}/confirm?token={token}"
    if not newsletter_ids or list(newsletter_ids) == [AI]:
        what = (f'<span style="color:#c8b89a;font-weight:700;">Latent SpaceMail</span>, '
                'a weekly AI research &amp; news briefing.')
    else:
        what = _newsletter_list_html(newsletter_ids) + "."
    body = (
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#9a9088;margin:0 0 16px 0;">'
        'Someone (hopefully you) asked to subscribe this address to '
        f'{what}'
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


def _addition_email_html(token: str, newsletter_ids) -> str:
    """Confirmation for adding newsletters to an address that already gets one: the form takes any address, so
    an addition needs a click from the inbox like a new sign-up does."""
    link = f"{SERVICE_BASE_URL}/confirm?token={token}"
    body = (
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#9a9088;margin:0 0 16px 0;">'
        'Someone (hopefully you) asked to add '
        f'{_newsletter_list_html(newsletter_ids)} to the newsletters this address receives.'
        '</p>'
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#9a9088;margin:0;">'
        'Tap <strong style="color:#c8b89a;">CONFIRM</strong> above or click the button below to start receiving it every Monday morning. '
        'Your current subscriptions stay as they are.'
        '</p>'
    )
    return _email_shell(
        headline_big="CONFIRM",
        headline_small="NEW NEWSLETTER",
        body_html=body,
        cta_text="CONFIRM SUBSCRIPTION",
        cta_link=link,
        footer_note="If this wasn't you, ignore this email — nothing is added and your subscriptions are unchanged.",
        tx_code="ADD",
    )


def _already_subscribed_email_html(token: str, name: str = NEWSLETTER_NAME) -> str:
    link = f"{FRONTEND_BASE_URL}/preferences.html?token={token}"
    body = (
        f'<p style="font-family:{_MONO};font-size:12px;line-height:1.95;color:#9a9088;margin:0 0 16px 0;">'
        'This address already has an active '
        f'<span style="color:#c8b89a;font-weight:700;">{html_lib.escape(name)}</span> subscription — no action needed.'
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


# ---------------------------------------------------------------------------
# Small HTML pages returned by the GET endpoints (clicked from emails)
# ---------------------------------------------------------------------------

def _full_page(title: str, body: str, status: int = 200,
               action: tuple[str, str] | list[tuple[str, str]] | None = None) -> HTMLResponse:
    """Full-fidelity page matching the website aesthetic — canvas, logo, card, fonts.

    `action` is an optional (post_url, button_label), or a list of them: the page then carries a
    form per action whose button POSTs there (the first is the main button, the others are shown
    smaller). Opening the page itself changes nothing."""
    base = FRONTEND_BASE_URL or ""
    actions = [action] if isinstance(action, tuple) else list(action or [])
    form = ""
    for n, (url, label) in enumerate(actions):
        style = ("btn-primary-action", "margin-top:18px;width:100%") if n == 0 else (
            "btn-secondary-link", "width:100%;background:none;border:0;cursor:pointer;font-family:inherit")
        form += (f'<form method="post" action="{html_lib.escape(url, quote=True)}">'
                 f'<button type="submit" class="{style[0]}" style="{style[1]}">'
                 f'{html_lib.escape(label)}</button></form>')
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
    {form}
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


NewsletterId = Annotated[str, Field(max_length=32)]


class SubscribeRequest(BaseModel):
    email: Annotated[str, Field(max_length=254)]
    prefs: Prefs = Prefs()
    send_latest: bool = False
    # Which newsletters to subscribe to; none given means the AI newsletter (the form before topics existed).
    newsletters: Annotated[list[NewsletterId], Field(min_length=1, max_length=10)] | None = None
    consent_text_version: Annotated[str, Field(max_length=40)] | None = None


class EmailOnlyRequest(BaseModel):
    email: Annotated[str, Field(max_length=254)]


class PreferencesUpdate(BaseModel):
    token: Annotated[str, Field(max_length=128)]
    prefs: Prefs | None = None
    # The complete list of newsletters to get from now on (empty = none); omitted = leave them as they are.
    newsletters: Annotated[list[NewsletterId], Field(max_length=10)] | None = None
    consent_text_version: Annotated[str, Field(max_length=40)] | None = None


class AuthPreferencesUpdate(BaseModel):
    prefs: Prefs | None = None
    newsletters: Annotated[list[NewsletterId], Field(max_length=10)] | None = None
    consent_text_version: Annotated[str, Field(max_length=40)] | None = None


class AuthUnsubscribeRequest(BaseModel):
    newsletter: NewsletterId = "ai"
    all: bool = False


class SectionRefineRequest(BaseModel):
    raw_topic: Annotated[str, Field(max_length=200)]


class CustomSectionItem(BaseModel):
    id:            Annotated[str, Field(max_length=64)]
    raw_input:     Annotated[str, Field(max_length=200)]
    refined_topic: Annotated[str, Field(max_length=200)]

    @field_validator("raw_input", "refined_topic")
    @classmethod
    def _no_markup(cls, v: str) -> str:
        # These strings are shown back to the user as section names. Titles never need angle brackets, and
        # a client can post anything here without going through /auth/sections/refine.
        if "<" in v or ">" in v:
            raise ValueError("angle brackets are not allowed")
        return v


MAX_ENABLED_SECTIONS = 20
MAX_CUSTOM_SECTIONS  = 10


class SectionConfig(BaseModel):
    enabled_sections: Annotated[list[str], Field(max_length=MAX_ENABLED_SECTIONS)] | None = None
    custom_sections:  Annotated[list[CustomSectionItem], Field(max_length=MAX_CUSTOM_SECTIONS)] = []

    @field_validator("enabled_sections")
    @classmethod
    def _unique_sections(cls, v):
        return None if v is None else list(dict.fromkeys(v))

    @field_validator("custom_sections")
    @classmethod
    def _unique_custom(cls, v):
        seen_ids, seen_topics, out = set(), set(), []
        for item in v:
            topic = item.refined_topic.strip().lower()
            if item.id in seen_ids or topic in seen_topics:
                continue
            seen_ids.add(item.id)
            seen_topics.add(topic)
            out.append(item)
        return out


class SectionConfigUpdate(BaseModel):
    section_config: SectionConfig


class GoogleExchangeRequest(BaseModel):
    exchange_code: Annotated[str, Field(max_length=128)]


# ---------------------------------------------------------------------------
# Subscriber counts: public /spots, owner-only /stats
# ---------------------------------------------------------------------------

_SPOTS_CACHE: dict = {"at": 0.0, "value": None}
SPOTS_CACHE_SECONDS = 60


@router.get("/spots")
@limiter.limit("60/minute")
def get_spots(request: Request):
    """Public subscriber count for the home page. Counts only; cached so page views don't each query Firestore."""
    now = time.monotonic()
    if _SPOTS_CACHE["value"] is None or now - _SPOTS_CACHE["at"] > SPOTS_CACHE_SECONDS:
        _SPOTS_CACHE["value"] = {"active": _active_subscriber_count(_db()), "max": MAX_SUBSCRIBERS}
        _SPOTS_CACHE["at"] = now
    return _SPOTS_CACHE["value"]


@router.get("/stats")
@limiter.limit("30/minute")
def get_stats(
    request: Request,
    token: Annotated[str, Query(max_length=128)] = "",
):
    """Owner only. The token goes in an Authorization: Bearer header (the query parameter still works)."""
    if not ADMIN_TOKEN:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    auth = request.headers.get("authorization", "")
    given = auth[7:].strip() if auth.lower().startswith("bearer ") else token
    if not hmac.compare_digest(given.encode("utf-8"), ADMIN_TOKEN.encode("utf-8")):
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    return {"active": _active_subscriber_count(_db()), "max": MAX_SUBSCRIBERS}


# ---------------------------------------------------------------------------
# SendGrid Event Webhook: aggregate click counts (see agents/sendgrid_webhook.py, click_counts.py)
# ---------------------------------------------------------------------------
# Public route, so the ECDSA signature is the only gate and is checked on the raw bytes before anything is
# parsed. Events carry subscriber emails, IPs and user agents: reduce_events() drops them before counting,
# and nothing in this handler may log or store the body or an event. Counts only, per shipped article.

_LINK_DOC_CACHE: dict = {}


def _cached_link_doc(db, run_id: str):
    """click_links doc for a run. Only hits are cached (agent4 writes the doc before any click can arrive)."""
    if run_id in _LINK_DOC_CACHE:
        return _LINK_DOC_CACHE[run_id]
    doc = click_counts.load_link_doc(db, run_id)
    if doc:
        if len(_LINK_DOC_CACHE) >= 20:
            _LINK_DOC_CACHE.clear()
        _LINK_DOC_CACHE[run_id] = doc
    return doc


def _count_clicks(events: list) -> int:
    db = _db()
    return click_counts.apply_tallies(db, click_counts.tally(events, lambda rid: _cached_link_doc(db, rid)))


@router.post("/sendgrid/events")
@limiter.limit("120/minute")
async def sendgrid_events(request: Request):
    body = await request.body()
    try:
        public_key = _get_sendgrid_webhook_public_key()
    except Exception:
        logger.error("sendgrid events: webhook public key is not configured")
        return JSONResponse(status_code=503, content={"error": "not_configured"})
    try:
        sendgrid_webhook.verify_signature(
            public_key,
            request.headers.get(sendgrid_webhook.SIGNATURE_HEADER),
            request.headers.get(sendgrid_webhook.TIMESTAMP_HEADER),
            body,
        )
    except sendgrid_webhook.SignatureError as e:
        logger.warning("sendgrid events rejected: %s", e)  # message is generic by design
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    try:
        events = sendgrid_webhook.reduce_events(body)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "bad_request"})
    counted = 0
    if events:
        try:
            counted = await run_in_threadpool(_count_clicks, events)
        except Exception as e:
            # Still 200: SendGrid would otherwise retry the whole batch. Class name only, never event content.
            logger.error("sendgrid events: counting failed (%s)", type(e).__name__)
    logger.info("sendgrid events: %d click events, %d counted", len(events), counted)
    return {"received": len(events), "counted": counted}


# ---------------------------------------------------------------------------
# Website-initiated endpoints (no token — always return 200)
# ---------------------------------------------------------------------------

@router.post("/subscribe")
@limiter.limit("5/minute")
@steady_response_time
def subscribe(request: Request, req: SubscribeRequest, user: dict | None = Depends(get_optional_user)):
    email = req.email.strip().lower()
    if not EMAIL_RE.match(email):
        # The one case where we don't pretend success: a malformed address
        # is a UX problem (typo), not an information leak.
        return JSONResponse(status_code=422, content={"error": "invalid email address"})
    try:
        wanted = _check_newsletters(req.newsletters, admin=bool(user) and _is_admin(user))
    except HTTPException:
        return JSONResponse(status_code=404, content={"error": "unknown_newsletter"})
    consent = subscriptions_model.consent_version(req.consent_text_version)

    db  = _db()
    ref = db.collection(SUBSCRIBERS_COLLECTION).document(email)
    doc = ref.get()
    current = subscriptions_model.subscribed_ids(doc.to_dict() if doc.exists else None)

    if current:
        # Already gets something — answer by email only (NOT via the HTTP response, which stays identical to the
        # new-subscriber case). Newsletters it doesn't get yet need a confirmation click for the addition.
        data = doc.to_dict()
        adding = [i for i in wanted if i not in current]
        if not mail_throttle.allow_mail(db, email):
            return {"status": "ok"}
        if not adding:
            name = _newsletter_names(wanted)
            _send_email(email, f"You're already subscribed to {name}",
                        _already_subscribed_email_html(data["token"], name))
            return {"status": "ok"}
        ref.update({
            "pending_newsletters":     adding,
            "pending_consent_version": consent,
            "pending_requested_at":    datetime.now(timezone.utc),
            "send_latest":             req.send_latest,
            "latest_sent":             False,
        })
        _send_email(email, f"Confirm your subscription to {_newsletter_names(adding)}",
                    _addition_email_html(data["token"], adding))
        return {"status": "ok"}

    # Enforce subscriber cap before accepting a new subscription.
    if _active_subscriber_count(db) >= MAX_SUBSCRIBERS:
        raise HTTPException(status_code=503, detail="service_unavailable")

    # Paced per address: a refused call looks the same to the caller and changes nothing, so a stranger's
    # pending confirmation link is not replaced by repeated requests either.
    if not mail_throttle.allow_mail(db, email):
        return {"status": "ok"}

    # New subscriber, or inactive one re-subscribing: (re)generate the token.
    # Note: regenerating invalidates links in previously sent emails — fine.
    token      = secrets.token_urlsafe(32)
    now        = datetime.now(timezone.utc)
    expires_at = now + CONFIRM_TOKEN_TTL
    fields = {
        "email":            email,
        "subscribed_at":    now,
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
        # Activated by /confirm, with the consent wording the form showed.
        "pending_newsletters":     wanted,
        "pending_consent_version": consent,
        "pending_requested_at":    now,
    }
    # Only a doc that was never confirmed may expire; a former subscriber's doc is the record of consent,
    # so its confirmed_at is kept and it gets no expiry.
    if doc.exists and doc.to_dict().get("confirmed_at"):
        del fields["confirmed_at"]
    else:
        fields["expires_at"] = now + UNCONFIRMED_TTL
    ref.set(fields, merge=True)

    _send_email(email, f"Confirm your subscription to {_newsletter_names(wanted)}",
                _confirm_email_html(token, wanted))
    return {"status": "ok"}


def _render_preview(data: dict, variant: str = "") -> str:
    """Turn a run doc's stored newsletter into HTML safe to show on the site (placeholders, image URLs, links)."""
    variants = data.get("newsletter_variants") or {}
    # Show the Canada-inclusive variant so visitors see the full scope of the newsletter.
    # Fall back through 0_0 and then the legacy newsletter_html field.
    html = variants.get(variant) or variants.get("0_1") or variants.get("0_0") or data.get("newsletter_html", "")
    subscribe_url = f"{FRONTEND_BASE_URL}/"
    html = html.replace("{{UNSUBSCRIBE_URL}}", subscribe_url)
    html = html.replace("{{PREFERENCES_URL}}", subscribe_url)

    # Normalize stale image base URLs from old pipeline runs to the canonical custom domain.
    # Old agent3 used FRONTEND_BASE_URL as the image base, producing two broken patterns:
    #   latentspacemail.web.app/newsletter/images/ (when FRONTEND_BASE_URL was unset)
    #   newsletter.lofeodo.com/newsletter/images/  (when FRONTEND_BASE_URL had no /newsletter suffix)
    html = html.replace(
        "https://latentspacemail.web.app/newsletter/images/",
        "https://newsletter.lofeodo.com/images/",
    )
    html = html.replace(
        "https://newsletter.lofeodo.com/newsletter/images/",
        "https://newsletter.lofeodo.com/images/",
    )

    # Open article links in a new tab (external sites block iframe embedding).
    html = re.sub(
        r'(<a\b(?:(?!target=)[^>])*?)(href="https?://[^"]*")',
        r'\1\2 target="_blank" rel="noopener noreferrer"',
        html,
    )

    return html


PREVIEW_CSP = (
    "default-src 'none'; img-src https: data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
    "sandbox allow-popups allow-popups-to-escape-sandbox; "
    "frame-ancestors https://latentspacemail.web.app https://latentspacemail.firebaseapp.com https://newsletter.lofeodo.com"
)


@router.get("/issues/latest")
@limiter.limit("30/minute")
def newsletter_preview(request: Request):
    """Return the latest *release* newsletter HTML for embedding in the preview iframe. Debug runs are never shown."""
    doc = run_kind.latest_run(_db(), FIRESTORE_COLLECTION)
    if doc is None:
        return HTMLResponse("<p>No issue available yet. Check back Monday!</p>", status_code=404,
                            headers={"Content-Security-Policy": PREVIEW_CSP})
    headers = {"Content-Security-Policy": PREVIEW_CSP}
    return HTMLResponse(content=_render_preview(doc.to_dict()), status_code=200, headers=headers)


@router.get("/issues")
@limiter.limit("30/minute")
def archive_list(request: Request):
    """Public: the flagged release issues, newest first. Debug runs and unflagged old runs never appear."""
    return {"issues": run_kind.list_releases(_db(), FIRESTORE_COLLECTION)}


@router.get("/issues/{run_id}")
@limiter.limit("30/minute")
def archive_issue(request: Request, run_id: str):
    """Public: one archived issue as HTML, rendered and fenced exactly like /issues/latest. 404 for anything that is not
    an explicitly flagged, composed release run, so a debug run cannot be fetched by guessing its id."""
    headers = {"Content-Security-Policy": PREVIEW_CSP}
    doc = _db().collection(FIRESTORE_COLLECTION).document(run_id).get() if ids.is_doc_id(run_id) else None
    data = doc.to_dict() if doc is not None and doc.exists else None
    if not run_kind.is_archived_release(data):
        return HTMLResponse("<p>Issue not found.</p>", status_code=404, headers=headers)
    return HTMLResponse(content=_render_preview(data), status_code=200, headers=headers)


_LATEST_ISSUES_CACHE: dict = {"at": 0.0, "value": None}


def _latest_issue_ids(db) -> dict[str, str]:
    """{topic: newest archived issue id}, cached like /spots so page views don't each scan pipeline_runs."""
    now = time.monotonic()
    if _LATEST_ISSUES_CACHE["value"] is None or now - _LATEST_ISSUES_CACHE["at"] > SPOTS_CACHE_SECONDS:
        _LATEST_ISSUES_CACHE["value"] = run_kind.latest_archived_by_topic(db, FIRESTORE_COLLECTION)
        _LATEST_ISSUES_CACHE["at"] = now
    return _LATEST_ISSUES_CACHE["value"]


def _newsletter_card(topic_id: str, latest: dict) -> dict:
    t = topics.get_topic(topic_id)
    return {
        "id":           t.id,
        "name":         t.name,
        "tagline":      t.tagline,
        "accent_color": t.accent_color,
        "spotlight":    t.spotlight,
        "sections":     [{"name": c.name, "icon": c.icon} for c in t.categories],
        "latest_issue": latest.get(t.id),
        "hidden":       not launch.is_public(t.id),
    }


@router.get("/newsletters")
@limiter.limit("30/minute")
def list_newsletters(request: Request, user: dict | None = Depends(get_optional_user)):
    """Public: the ready-made newsletters this caller may see (hidden topics too for an admin, flagged `hidden`),
    AI first, plus the consent wording every signup form shows next to the newsletter names."""
    admin = bool(user) and _is_admin(user)
    latest = _latest_issue_ids(_db())
    return {
        "newsletters":          [_newsletter_card(i, latest) for i in launch.visible_topic_ids(admin)],
        "consent_text":         subscriptions_model.CONSENT_TEXT,
        "consent_text_version": subscriptions_model.CONSENT_CURRENT,
    }


@router.get("/auth/admin/topics")
@limiter.limit("30/minute")
def admin_topics(request: Request, user: dict = Depends(get_verified_user)):
    """Admin only: the newsletter topics (topics/*.yaml) a debug run can belong to, the AI newsletter first."""
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="forbidden")
    found = [topics.get_topic(t) for t in topics.list_topic_ids()]
    found.sort(key=lambda t: (not t.is_default, t.name.lower()))
    return {"topics": [{"id": t.id, "name": t.name, "tagline": t.tagline} for t in found]}


@router.get("/auth/admin/issues")
@limiter.limit("30/minute")
def admin_preview(
    request: Request,
    run: Annotated[str, Query(max_length=64)] = "latest",
    variant: Annotated[str, Query(max_length=8)] = "",
    topic: Annotated[str, Query(max_length=32)] = "",
    user: dict = Depends(get_verified_user),
):
    """Admin only: any run, debug ones included. `run` is "latest" (newest composed run of one topic, any kind;
    the AI newsletter unless `topic` says otherwise) or a run id."""
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="forbidden")
    if topic and not topics.is_valid_topic(topic):
        raise HTTPException(status_code=400, detail="unknown_topic")
    db = _db()
    if run == "latest":
        other = topics.get_topic(topic).id if topic else topics.DEFAULT_TOPIC
        doc = (run_kind.latest_run(db, FIRESTORE_COLLECTION, release_only=False) if other == topics.DEFAULT_TOPIC
               else run_kind.latest_run(db, FIRESTORE_COLLECTION, release_only=False, topic=other))
    elif ids.is_doc_id(run):
        doc = db.collection(FIRESTORE_COLLECTION).document(run).get()
        doc = doc if doc.exists else None
    else:
        doc = None
    if doc is None:
        raise HTTPException(status_code=404, detail="no_run")
    data = doc.to_dict()
    run_topic = topics.topic_id_of(data)
    return {
        "run_id":     doc.id,
        "run_kind":   data.get("run_kind", run_kind.RELEASE),
        "topic":      run_topic,
        "topic_name": topics.get_topic(run_topic).name if topics.is_valid_topic(run_topic) else run_topic,
        "html":       _render_preview(data, variant),
    }


@router.post("/request-unsubscribe")
@limiter.limit("5/minute")
@steady_response_time
def request_unsubscribe(request: Request, req: EmailOnlyRequest):
    email = ids.normalize_email(req.email)
    if email is None:
        return {"status": "ok"}
    db  = _db()
    doc = db.collection(SUBSCRIBERS_COLLECTION).document(email).get()

    if doc.exists and doc.to_dict().get("active") and mail_throttle.allow_mail(db, email):
        _send_email(email, f"Confirm unsubscription from {NEWSLETTER_NAME}",
                    _unsubscribe_email_html(doc.to_dict()["token"]))
    # Same response whether or not the subscriber exists.
    return {"status": "ok"}


@router.post("/request-preferences")
@limiter.limit("5/minute")
@steady_response_time
def request_preferences(request: Request, req: EmailOnlyRequest):
    email = ids.normalize_email(req.email)
    if email is None:
        return {"status": "ok"}
    db  = _db()
    doc = db.collection(SUBSCRIBERS_COLLECTION).document(email).get()

    if doc.exists and doc.to_dict().get("active") and mail_throttle.allow_mail(db, email):
        _send_email(email, f"Manage your {NEWSLETTER_NAME} preferences",
                    _preferences_email_html(doc.to_dict()["token"]))
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Token-carrying endpoints (clicked from inside an email)
# ---------------------------------------------------------------------------

@router.get("/confirm")
@limiter.limit("10/minute")
def confirm_page(
    request: Request,
    token: Annotated[str, Query(max_length=128)] = "",
):
    """Opening the link only shows a button; the sign-up is activated by the POST below."""
    if not token or not re.match(r'^[A-Za-z0-9_-]+$', token):
        return _INVALID_LINK_PAGE
    if _find_by_token(_db(), token, enforce_expiry=True) is None:
        return _INVALID_LINK_PAGE
    return _full_page("Confirm your subscription",
                      f"One more step: press the button to start receiving {NEWSLETTER_NAME}.",
                      action=(f"{SERVICE_BASE_URL}/confirm?{urlencode({'token': token})}",
                              "Confirm subscription"))


@router.post("/confirm")
@limiter.limit("10/minute")
def confirm(
    request: Request,
    token: Annotated[str, Query(max_length=128)] = "",
):
    if not token or not re.match(r'^[A-Za-z0-9_-]+$', token):
        return _INVALID_LINK_PAGE

    db  = _db()
    # A pending sign-up's link expires (48h); an address that already gets a newsletter holds a long-lived action
    # token, and a pending addition on it has its own window (pending_requested_at), checked below.
    doc = _find_by_token(db, token)
    if doc is None:
        return _INVALID_LINK_PAGE

    data    = doc.to_dict()
    current = subscriptions_model.subscribed_ids(data)
    raw     = data.get("pending_newsletters")
    pending = [i for i in raw if isinstance(i, str) and topics.is_valid_topic(i)] if isinstance(raw, list) else []
    if not current:
        adding = pending or [AI]   # a sign-up made before newsletters existed has no list: the AI newsletter
    else:
        adding = [i for i in pending if i not in current] if _pending_is_fresh(data) else []

    if not adding:
        # A repeat click, a double click or a mail scanner's visit: already done, nothing more to do.
        return _full_page("Subscription confirmed 🎉",
                          f"You'll receive {html_lib.escape(_newsletter_names(current))} every Monday morning.")

    # Guard against the race condition where two people hold confirmation
    # links for the last open spot and both click at the same time.
    if not current and _active_subscriber_count(db) >= MAX_SUBSCRIBERS:
        return _full_page(
            "Newsletter is full",
            "All spots were claimed just before you confirmed. Check back later.",
            status=503,
        )

    # The link's token stays: the same inbox holds it, and a second visit must still find the subscriber.
    from google.cloud import firestore as _fs
    now     = datetime.now(timezone.utc)
    consent = subscriptions_model.consent_version(data.get("pending_consent_version"))
    merged  = dict(data)
    for i in adding:
        merged.update(subscriptions_model.add(merged, i, consent, now))
    fields = {k: merged[k] for k in ("newsletters", "newsletter_meta", "active", "subscribed_any")}
    fields.update({k: _fs.DELETE_FIELD for k in _PENDING_FIELDS if k in data})
    if not current:
        fields.update({
            "confirmed_at":      now,   # CASL proof of consent
            "token_expires_at":  now + ACTION_TOKEN_TTL,
            "expires_at":        _fs.DELETE_FIELD,
        })
    doc.reference.update(fields)
    print(f"[subscriptions]  confirmed subscriber ({len(adding)} newsletter(s))", flush=True)

    # Send last week's issue of each newsletter if they asked for it, once: the flag is claimed before sending.
    if data.get("send_latest") and _claim_latest(db, doc.reference):
        try:
            if not _send_latest_editions(db, data["email"], data["token"], data.get("prefs") or {}, adding):
                doc.reference.update({"latest_sent": False})
        except Exception:
            doc.reference.update({"latest_sent": False})
            raise

    return _full_page("Subscription confirmed 🎉",
                 f"You'll receive {html_lib.escape(_newsletter_names(adding))} every Monday morning.")


_PENDING_FIELDS = ("pending_newsletters", "pending_consent_version", "pending_requested_at")


def _pending_is_fresh(data: dict) -> bool:
    """A pending addition counts for CONFIRM_TOKEN_TTL after it was requested, like a new sign-up's link."""
    at = data.get("pending_requested_at")
    if not at:
        return False
    if getattr(at, "tzinfo", None) is None:
        at = at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) <= at + CONFIRM_TOKEN_TTL


@router.get("/unsubscribe")
@limiter.limit("10/minute")
def unsubscribe_page(
    request: Request,
    token: Annotated[str, Query(max_length=128)] = "",
    n: Annotated[str, Query(max_length=32)] = "",
):
    """Opening the link only shows a button; the unsubscribe happens on the POST below. `n` names the newsletter
    the link came from; a link without it (every AI email, every email sent before topics) means the AI one."""
    newsletter = _link_newsletter(n)
    if not token or not re.match(r'^[A-Za-z0-9_-]+$', token) or newsletter is None:
        return _INVALID_LINK_PAGE
    doc = _find_by_token(_db(), token)
    if doc is None:
        return _INVALID_LINK_PAGE
    others = [i for i in subscriptions_model.subscribed_ids(doc.to_dict()) if i != newsletter]
    name = _newsletter_name(newsletter)   # button labels are escaped by _full_page; page text is escaped here
    one = (f"{SERVICE_BASE_URL}/unsubscribe?{_link_query(token, newsletter)}", "Unsubscribe")
    if not others:
        return _full_page("Unsubscribe",
                          f"Press the button to stop receiving {html_lib.escape(name)}. You can re-subscribe any time.",
                          action=one)
    everything = (f"{SERVICE_BASE_URL}/unsubscribe?{urlencode({'token': token, 'all': '1'})}",
                  "Unsubscribe from everything")
    return _full_page("Unsubscribe",
                      f"Press the button to stop receiving {html_lib.escape(name)}. "
                      f"You also get {html_lib.escape(_newsletter_names(others))}, which stay as they are unless "
                      "you unsubscribe from everything. You can re-subscribe any time.",
                      action=[(one[0], f"Unsubscribe from {name}"), everything])


def _link_newsletter(n: str) -> str | None:
    """The newsletter an emailed link names: AI when absent, None when it is not a known newsletter. Hidden topics
    count: their links reach admins before launch and must keep working after an un-launch."""
    n = (n or AI).strip().lower()
    return topics.get_topic(n).id if topics.is_valid_topic(n) else None


@router.post("/unsubscribe")
@limiter.limit("10/minute")
def unsubscribe(
    request: Request,
    token: Annotated[str, Query(max_length=128)] = "",
    n: Annotated[str, Query(max_length=32)] = "",
    everything: Annotated[str, Query(alias="all", max_length=1)] = "",
):
    newsletter = _link_newsletter(n)
    if not token or not re.match(r'^[A-Za-z0-9_-]+$', token) or newsletter is None:
        return _INVALID_LINK_PAGE

    db  = _db()
    doc = _find_by_token(db, token)
    if doc is None:
        return _INVALID_LINK_PAGE

    data = doc.to_dict()
    if everything == "1":
        from google.cloud import firestore as _fs
        fields = subscriptions_model.remove_all(data)
        fields.update({k: _fs.DELETE_FIELD for k in _PENDING_FIELDS if k in data})
        doc.reference.update(fields)
        print("[subscriptions]  unsubscribed subscriber from everything", flush=True)
        return _full_page("You've been unsubscribed",
                          "You won't receive any of our newsletters anymore. You can re-subscribe any time.")

    doc.reference.update(subscriptions_model.remove(data, newsletter))
    print(f"[subscriptions]  unsubscribed subscriber from {newsletter}", flush=True)
    others = [i for i in subscriptions_model.subscribed_ids(data) if i != newsletter]
    if others:
        return _full_page("You've been unsubscribed",
                          f"You won't receive {html_lib.escape(_newsletter_name(newsletter))} anymore. "
                          f"Your other newsletters ({html_lib.escape(_newsletter_names(others))}) are unchanged.")
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
    return {"email": data["email"], "prefs": data.get("prefs") or {}, **_newsletter_choices(data, admin=False)}


@router.post("/preferences")
@limiter.limit("10/minute")
def update_preferences(request: Request, req: PreferencesUpdate):
    if not req.token or not re.match(r'^[A-Za-z0-9_-]+$', req.token):
        return JSONResponse(status_code=404, content={"error": "not_found"})

    db  = _db()
    doc = _find_by_token(db, req.token)
    if doc is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})

    try:
        fields = _preference_fields(db, doc.to_dict(), req.prefs, req.newsletters, req.consent_text_version,
                                    admin=False)
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"error": e.detail})
    if fields:
        doc.reference.update(fields)
    return {"status": "ok"}


def _newsletter_choices(data: dict | None, admin: bool) -> dict:
    """What a preferences page shows: the newsletters this address gets, and every one it could get (the visible
    ones plus any it already has, so an admin's pre-launch subscription still shows on the emailed link)."""
    current = subscriptions_model.subscribed_ids(data)
    ids = launch.ordered(set(launch.visible_topic_ids(admin)) | set(current))
    return {
        "newsletters": current,
        "available": [{"id": t.id, "name": t.name, "tagline": t.tagline, "accent_color": t.accent_color,
                       "hidden": not launch.is_public(t.id)} for t in map(topics.get_topic, ids)],
    }


def _preference_fields(db, data: dict, prefs: Prefs | None, newsletters, consent, admin: bool) -> dict:
    """Fields to write for a preferences update. The token (or the signed-in account) proves the inbox, so both
    removing and adding a newsletter are instant. Adding one the caller cannot see is a 404; the first newsletter
    of someone who had none is a new person under the cap (503 when full)."""
    fields = {}
    if prefs is not None:
        fields["prefs"] = {"include_french": prefs.include_french, "include_canada": prefs.include_canada}
    if newsletters is None:
        return fields
    current = subscriptions_model.subscribed_ids(data)
    wanted = list(dict.fromkeys(str(i).strip().lower() for i in newsletters))
    allowed = set(launch.visible_topic_ids(admin)) | set(current)
    if any(i not in allowed for i in wanted):
        raise HTTPException(status_code=404, detail="unknown_newsletter")
    adds = [i for i in wanted if i not in current]
    if not current and adds and _active_subscriber_count(db) >= MAX_SUBSCRIBERS:
        raise HTTPException(status_code=503, detail="service_unavailable")
    merged, now = dict(data), datetime.now(timezone.utc)
    version = subscriptions_model.consent_version(consent)
    for i in current:
        if i not in wanted:
            merged.update(subscriptions_model.remove(merged, i, now))
    for i in adds:
        merged.update(subscriptions_model.add(merged, i, version, now))
    if adds or len(wanted) != len(current):
        fields.update({k: merged[k] for k in ("newsletters", "newsletter_meta", "active", "subscribed_any")})
    if adds and not current and data.get("expires_at"):
        # A never-confirmed doc: its emailed link proves the inbox just as the confirm button does, so it stops
        # being a pending sign-up (otherwise the TTL rule on expires_at would delete a real subscriber).
        from google.cloud import firestore as _fs
        fields.update({"confirmed_at": now, "expires_at": _fs.DELETE_FIELD,
                       "token_expires_at": now + ACTION_TOKEN_TTL})
    return fields


# ---------------------------------------------------------------------------
# Latest newsletter lookup (same composite-index query as agent4, via run_kind)
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


def _send_latest_editions(db, email: str, token: str, prefs: dict, newsletter_ids) -> int:
    """Mail the newest sent issue of each newsletter that has one; returns how many went out."""
    sent = 0
    for i in newsletter_ids:
        html = _latest_newsletter_html(db, unsubscribe_token=token, prefs=prefs, topic=i)
        if html:
            _send_email(email, f"{_newsletter_name(i)} — last week's edition", html)
            sent += 1
    return sent


def _latest_newsletter_html(db, unsubscribe_token: str, prefs: dict | None = None, topic: str = AI):
    # The newest issue of this newsletter that was actually mailed: never a debug run, and not a draft still
    # waiting for Monday. A newsletter with no sent issue yet sends nothing.
    doc = run_kind.latest_run(db, FIRESTORE_COLLECTION, sent_only=True, topic=topic)
    if doc is None:
        print("[subscriptions]  no newsletter found to send", flush=True)
        return None

    data     = doc.to_dict()
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
    unsub_link = f"{SERVICE_BASE_URL}/unsubscribe?{_link_query(unsubscribe_token, topic)}"
    prefs_link = f"{FRONTEND_BASE_URL}/preferences.html?{_link_query(unsubscribe_token, topic)}"
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
def auth_me(request: Request, user: dict = Depends(get_current_user)):
    """Return the current user's profile and subscription status."""
    uid = user["uid"]
    if not user.get("email_verified"):
        return {
            "uid": uid, "email": (user.get("email") or "").lower(),
            "display_name": user.get("name"), "email_verified": False,
            "subscribed": False, "prefs": {}, "tier": "free", "is_admin": False,
        }
    db    = _db()
    email = user.get("email", "").lower()
    now   = datetime.now(timezone.utc)
    tier  = _get_user_tier(user)

    user_ref = db.collection(USERS_COLLECTION).document(uid)
    profile = {
        "email":        email,
        "display_name": user.get("name"),
        "provider":     _detect_provider(user),
        "tier":         tier,
    }
    if not (user_ref.get().to_dict() or {}).get("created_at"):
        profile["created_at"] = now
    user_ref.set(profile, merge=True)

    sub_doc  = db.collection(SUBSCRIBERS_COLLECTION).document(email).get()
    sub_data = sub_doc.to_dict() if sub_doc.exists else None
    return {
        "uid":            uid,
        "email":          email,
        "display_name":   user.get("name"),
        "email_verified": user.get("email_verified", False),
        "subscribed":     bool(sub_data and sub_data.get("active")),   # the AI newsletter, as before
        "newsletters":    subscriptions_model.subscribed_ids(sub_data),
        "prefs":          (sub_data.get("prefs") or {}) if sub_data else {},
        "tier":           tier,
        "is_admin":       _is_admin(user),
    }


@router.post("/auth/subscribe")
@limiter.limit("10/minute")
async def auth_subscribe(request: Request, user: dict = Depends(get_verified_user)):
    """Subscribe the authenticated user. No confirmation email — account email is already verified."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    send_latest = bool(body.get("send_latest", False))
    newsletter  = body.get("newsletter") or AI
    if not isinstance(newsletter, str) or len(newsletter) > 32:
        raise HTTPException(status_code=422, detail="invalid_newsletter")
    consent = body.get("consent_text_version")
    return await run_in_threadpool(_subscribe_account, user, send_latest, newsletter,
                                   consent if isinstance(consent, str) else None)


def _subscribe_account(user: dict, send_latest: bool, newsletter: str = AI, consent: str | None = None) -> dict:
    newsletter = _check_newsletters([newsletter], admin=_is_admin(user))[0]
    db    = _db()
    email = user["email"].lower()
    uid   = user["uid"]
    now   = datetime.now(timezone.utc)

    ref = db.collection(SUBSCRIBERS_COLLECTION).document(email)
    doc = ref.get()
    data = doc.to_dict() if doc.exists else None
    current = subscriptions_model.subscribed_ids(data)

    if newsletter in current:
        return {"status": "already_subscribed"}

    # The cap counts people: someone who already gets a newsletter is not a new one.
    if not current and _active_subscriber_count(db) >= MAX_SUBSCRIBERS:
        raise HTTPException(status_code=503, detail="service_unavailable")

    fields = subscriptions_model.add(data, newsletter, subscriptions_model.consent_version(consent), now)
    if doc.exists:
        from google.cloud import firestore as _fs
        fields.update({
            "uid":              uid,
            "send_latest":      send_latest,
            "latest_sent":      False,
            **{k: _fs.DELETE_FIELD for k in _PENDING_FIELDS if k in data},
        })
        if not current:
            # Coming back (or confirming a pending sign-up through the account): a fresh link, as before. Someone
            # still subscribed to another newsletter keeps theirs, so links in emails already sent keep working.
            fields.update({
                "confirmed_at":     now,
                "expires_at":       _fs.DELETE_FIELD,
                "token":            secrets.token_urlsafe(32),
                "token_expires_at": now + ACTION_TOKEN_TTL,
            })
        ref.update(fields)
    else:
        ref.set({
            "email":            email,
            "subscribed_at":    now,
            "confirmed_at":     now,
            "token":            secrets.token_urlsafe(32),
            "token_expires_at": now + ACTION_TOKEN_TTL,
            "send_latest":      send_latest,
            "latest_sent":      False,
            "uid":              uid,
            "prefs": {"include_french": True, "include_canada": True},
            **fields,
        })

    if send_latest:
        current_data = ref.get().to_dict()
        html = _latest_newsletter_html(
            db,
            unsubscribe_token=current_data["token"],
            prefs=current_data.get("prefs") or {},
            topic=newsletter,
        )
        if html:
            _send_email(email, f"{_newsletter_name(newsletter)} — last week's edition", html)
            ref.update({"latest_sent": True})

    print(f"[subscriptions]  account-based subscribe: {email}", flush=True)
    return {"status": "ok"}


@router.post("/auth/unsubscribe")
@limiter.limit("10/minute")
def auth_unsubscribe(request: Request, req: AuthUnsubscribeRequest | None = None,
                     user: dict = Depends(get_verified_user)):
    """Unsubscribe the authenticated user from one newsletter (the AI one when no body is sent) or from all."""
    req   = req or AuthUnsubscribeRequest()
    db    = _db()
    email = user["email"].lower()
    ref   = db.collection(SUBSCRIBERS_COLLECTION).document(email)
    snap  = ref.get()
    if snap.exists:
        data = snap.to_dict()
        newsletter = req.newsletter.strip().lower()
        ref.update(subscriptions_model.remove_all(data) if req.all else subscriptions_model.remove(data, newsletter))
    print(f"[subscriptions]  account-based unsubscribe: {email}", flush=True)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Google Sign-In — server-side OAuth Authorization Code flow.
#
# Google Sign-In on mobile Safari was broken across 9 client-side Firebase SDK
# attempts (signInWithPopup, signInWithRedirect, various initializeAuth
# configs — see CLAUDE.md history). The likely root cause is architectural:
# Firebase's redirect flow depends on writing "a redirect is pending" state to
# IndexedDB immediately before navigating to accounts.google.com and reading
# it back via getRedirectResult() after the round trip — exactly the kind of
# cross-site-navigation-adjacent storage Safari's ITP partitions/evicts.
#
# This flow avoids that entirely: the backend does the whole OAuth negotiation
# server-to-server, and the only signal the frontend needs ("did sign-in
# succeed") is a plain URL query parameter on a redirect — not storage of any
# kind, so ITP's model doesn't apply to it.
#
#   GET  /auth/google/login    — redirect the browser to Google's consent screen
#   GET  /auth/google/callback — Google redirects back here with a code
#   POST /auth/google/exchange — frontend redeems a one-time code for a
#                                 Firebase custom token
# ---------------------------------------------------------------------------

@router.get("/auth/google/login")
@limiter.limit("10/minute")
def auth_google_login(request: Request, return_to: Annotated[str, Query(max_length=128)] = "") -> RedirectResponse:
    if not GOOGLE_OAUTH_CLIENT_ID or not SERVICE_BASE_URL:
        return RedirectResponse(
            f"{FRONTEND_BASE_URL}/login.html?auth_error=google_signin_unavailable", status_code=302
        )

    return_to = return_to if return_to in ALLOWED_RETURN_PAGES else DEFAULT_RETURN_PAGE
    state = secrets.token_urlsafe(32)

    db = _db()
    db.collection(OAUTH_STATES_COLLECTION).document(state).set({
        "return_to":  return_to,
        "expires_at": datetime.now(timezone.utc) + OAUTH_STATE_TTL,
    })

    params = {
        "client_id":     GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri":  f"{SERVICE_BASE_URL}/auth/google/callback",
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         state,
        "access_type":   "online",
        "prompt":        "select_account",
    }
    resp = RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}", status_code=302)
    # SameSite=Lax so the cookie rides along on the top-level GET navigation
    # back from accounts.google.com. This is a first-party cookie throughout
    # (this service sets it and reads it back) — never a cross-site cookie
    # read, so it isn't subject to the third-party storage restrictions this
    # whole rebuild exists to avoid. Checked in the callback below to bind the
    # OAuth state to *this* browser, not just to "some" browser that started a
    # flow (RFC 6749 §10.12 login-CSRF).
    resp.set_cookie("oauth_state", state, max_age=600, httponly=True, secure=True, samesite="lax")
    return resp


@router.get("/auth/google/callback")
@limiter.limit("10/minute")
def auth_google_callback(
    request: Request,
    code: Annotated[str, Query(max_length=512)] = "",
    state: Annotated[str, Query(max_length=128)] = "",
    error: Annotated[str, Query(max_length=128)] = "",
) -> RedirectResponse:
    def _fail(reason: str, return_to: str = DEFAULT_RETURN_PAGE) -> RedirectResponse:
        logger.warning("Google sign-in failed: %s", reason)
        resp = RedirectResponse(
            f"{FRONTEND_BASE_URL}/login.html?auth_error=google_signin_failed&returnUrl={return_to}",
            status_code=302,
        )
        resp.delete_cookie("oauth_state")
        return resp

    db = _db()
    state_data = _consume_once(db, OAUTH_STATES_COLLECTION, state) if ids.is_doc_id(state) else None
    return_to  = state_data.get("return_to", DEFAULT_RETURN_PAGE) if state_data else DEFAULT_RETURN_PAGE

    if error or state_data is None or request.cookies.get("oauth_state") != state:
        return _fail("state_invalid", return_to)

    try:
        token_res = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code":          code,
                "client_id":     GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": _get_google_oauth_client_secret(),
                "redirect_uri":  f"{SERVICE_BASE_URL}/auth/google/callback",
                "grant_type":    "authorization_code",
            },
            timeout=10,
        )
        token_res.raise_for_status()

        import google.oauth2.id_token
        import google.auth.transport.requests as google_auth_requests
        claims = google.oauth2.id_token.verify_oauth2_token(
            token_res.json()["id_token"], google_auth_requests.Request(), audience=GOOGLE_OAUTH_CLIENT_ID,
        )
    except Exception:
        logger.exception("Google OAuth code exchange/verification failed")
        return _fail("exchange_failed", return_to)

    email = (claims.get("email") or "").lower()
    if not email or not claims.get("email_verified"):
        return _fail("email_not_verified", return_to)

    from auth_middleware import _ensure_firebase
    _ensure_firebase()
    from firebase_admin import auth as fb_auth

    try:
        fb_user = fb_auth.get_user_by_email(email)
        if not fb_user.email_verified:
            # Google just proved ownership of this address. An existing-but-
            # unverified password account could be the real user (never
            # clicked verify) or an attacker who pre-registered the victim's
            # email with a password of their choosing ("pre-hijacking"). Either
            # way, the old password must stop working: reset it to a random
            # value and revoke existing sessions rather than trust it further.
            fb_auth.update_user(fb_user.uid, email_verified=True, password=secrets.token_urlsafe(32))
            fb_auth.revoke_refresh_tokens(fb_user.uid)
    except fb_auth.UserNotFoundError:
        try:
            fb_user = fb_auth.create_user(
                email=email, email_verified=True,
                display_name=claims.get("name"), photo_url=claims.get("picture"),
            )
        except fb_auth.EmailAlreadyExistsError:
            fb_user = fb_auth.get_user_by_email(email)  # lost a create race to a concurrent request

    # Persistent custom claim — NOT create_custom_token's ephemeral
    # developer_claims, which would silently disappear after Firebase's
    # ~hourly background ID-token refresh.
    fb_auth.set_custom_user_claims(fb_user.uid, {"provider": "google"})

    exchange_code = secrets.token_urlsafe(32)
    db.collection(OAUTH_EXCHANGE_CODES_COLLECTION).document(exchange_code).set({
        "uid":        fb_user.uid,
        "expires_at": datetime.now(timezone.utc) + OAUTH_EXCHANGE_CODE_TTL,
    })

    resp = RedirectResponse(
        f"{FRONTEND_BASE_URL}/auth-callback.html?exchange_code={exchange_code}&return_to={return_to}",
        status_code=302,
    )
    resp.delete_cookie("oauth_state")
    return resp


@router.post("/auth/google/exchange")
@limiter.limit("10/minute")
def auth_google_exchange(request: Request, req: GoogleExchangeRequest) -> dict:
    """Redeem a one-time exchange code (minted by /auth/google/callback) for a
    Firebase custom token. This is the only step of the flow reached via
    fetch() rather than a top-level navigation, so it's the only one that
    needs CORS — already covered since it's just another route on `router`.
    """
    if not re.match(r'^[A-Za-z0-9_-]+$', req.exchange_code):
        raise HTTPException(status_code=404, detail="invalid_or_expired_code")

    db   = _db()
    data = _consume_once(db, OAUTH_EXCHANGE_CODES_COLLECTION, req.exchange_code)
    if data is None:
        raise HTTPException(status_code=404, detail="invalid_or_expired_code")

    from auth_middleware import _ensure_firebase
    _ensure_firebase()
    from firebase_admin import auth as fb_auth
    custom_token = fb_auth.create_custom_token(data["uid"]).decode("utf-8")  # bytes -> str for JSON
    return {"custom_token": custom_token}


@router.get("/auth/preferences")
@limiter.limit("10/minute")
def auth_get_preferences(request: Request, user: dict = Depends(get_verified_user)):
    """Return the authenticated user's current preferences and tier."""
    db    = _db()
    email = user["email"].lower()
    tier  = _get_user_tier(user)
    doc   = db.collection(SUBSCRIBERS_COLLECTION).document(email).get()
    data  = doc.to_dict() if doc.exists else None
    choices = _newsletter_choices(data, admin=_is_admin(user))
    if not data or not data.get("active"):
        return {"subscribed": False, "prefs": {}, "tier": tier, **choices}
    return {"subscribed": True, "prefs": data.get("prefs") or {}, "tier": tier, **choices}


@router.post("/auth/preferences")
@limiter.limit("10/minute")
def auth_update_preferences(
    request: Request,
    req: AuthPreferencesUpdate,
    user: dict = Depends(get_verified_user),
):
    """Update the authenticated user's preferences."""
    db    = _db()
    email = user["email"].lower()
    ref   = db.collection(SUBSCRIBERS_COLLECTION).document(email)
    snap  = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="not_subscribed")
    fields = _preference_fields(db, snap.to_dict(), req.prefs, req.newsletters, req.consent_text_version,
                                admin=_is_admin(user))
    if fields:
        ref.update(fields)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Premium: custom section endpoints
# ---------------------------------------------------------------------------

@router.post("/auth/sections/refine")
@limiter.limit("5/minute")
def auth_sections_refine(
    request: Request,
    req: SectionRefineRequest,
    user: dict = Depends(get_verified_user),
):
    """Use Claude to refine a free-form topic into a clean newsletter section title."""
    email = user.get("email", "").lower()
    if _get_user_tier(user) != "premium":
        raise HTTPException(status_code=403, detail="premium_required")

    raw = req.raw_topic.strip()
    try:
        client = anthropic.Anthropic(api_key=_get_anthropic_api_key(), timeout=REFINE_TIMEOUT_SECONDS)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            system=GUARD_TOPIC_REFINE,
            messages=[{
                "role": "user",
                "content": (
                    f'A newsletter subscriber wants a custom section about: <topic>{neutralize_tags(raw)}</topic>\n'
                    "Return only a concise newsletter section title (4–8 words) that clearly captures "
                    "what they want covered — e.g. \"SpaceX product launches & mission updates\". "
                    "No explanation, just the title."
                ),
            }],
        )
        refined = re.sub(r"[<>]", "", message.content[0].text).strip().strip("\"'")
    except Exception:
        logger.exception("Refine endpoint error for user %s", user.get("uid"))
        raise HTTPException(status_code=503, detail="ai_unavailable")

    return {"refined_topic": refined}


@router.get("/auth/sections")
@limiter.limit("10/minute")
def auth_get_sections(request: Request, user: dict = Depends(get_verified_user)):
    """Return the user's section configuration (premium only)."""
    email = user.get("email", "").lower()
    if _get_user_tier(user) != "premium":
        raise HTTPException(status_code=403, detail="premium_required")

    uid      = user["uid"]
    db       = _db()
    user_doc = db.collection(USERS_COLLECTION).document(uid).get()
    section_config = user_doc.to_dict().get("section_config") if user_doc.exists else None
    return {"default_sections": DEFAULT_SECTIONS, "section_config": section_config}


@router.post("/auth/sections")
@limiter.limit("10/minute")
def auth_update_sections(
    request: Request,
    req: SectionConfigUpdate,
    user: dict = Depends(get_verified_user),
):
    """Save the user's section configuration (premium only)."""
    email = user.get("email", "").lower()
    if _get_user_tier(user) != "premium":
        raise HTTPException(status_code=403, detail="premium_required")

    if req.section_config.enabled_sections is not None:
        valid = set(DEFAULT_SECTIONS)
        for s in req.section_config.enabled_sections:
            if s not in valid:
                raise HTTPException(status_code=422, detail="unknown_section")

    uid = user["uid"]
    db  = _db()
    db.collection(USERS_COLLECTION).document(uid).set({
        "section_config": {
            "enabled_sections": req.section_config.enabled_sections,
            "custom_sections": [
                {"id": cs.id, "raw_input": cs.raw_input, "refined_topic": cs.refined_topic}
                for cs in req.section_config.custom_sections
            ],
        }
    }, merge=True)
    return {"status": "ok"}