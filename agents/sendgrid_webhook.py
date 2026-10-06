# agents/sendgrid_webhook.py
#
# Pure helpers for SendGrid's signed Event Webhook (no Firestore, no network, no FastAPI).
#
# 1. verify_signature(): ECDSA (P-256, SHA-256) over timestamp + raw body, exactly as SendGrid signs it
#    (headers X-Twilio-Email-Event-Webhook-Signature / -Timestamp, public key from the SendGrid console).
#    It must run on the raw bytes BEFORE anything is parsed: the signature is the only gate on a public route.
# 2. reduce_events(): turns the batch into the few fields click counting needs and drops everything else.
#    Click events carry the recipient's email, IP and user agent. They are read here only to decide
#    "is this a bot" and are never copied into the output, so nothing downstream (storage, logs) can
#    contain them. Do not log or print the body or an event anywhere.

import base64
import binascii
import json
import os
import sys
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

SIGNATURE_HEADER = "X-Twilio-Email-Event-Webhook-Signature"
TIMESTAMP_HEADER = "X-Twilio-Email-Event-Webhook-Timestamp"

_MAX_URL = 2048
_MAX_RUN_ID = 128


class SignatureError(Exception):
    """The request is not a correctly signed, fresh SendGrid webhook call. Message is safe to log."""


def verify_signature(public_key_b64: str, signature_b64: str | None, timestamp: str | None, body: bytes,
                     now: float | None = None, tolerance_seconds: int | None = None) -> None:
    """Raise SignatureError unless `body` was signed by the holder of the matching private key, recently."""
    tolerance = config.SENDGRID_WEBHOOK_TOLERANCE_SECONDS if tolerance_seconds is None else tolerance_seconds
    if not signature_b64 or not timestamp:
        raise SignatureError("missing signature or timestamp header")
    try:
        ts = int(timestamp)
    except ValueError:
        raise SignatureError("timestamp is not an integer") from None
    if abs((time.time() if now is None else now) - ts) > tolerance:
        raise SignatureError("timestamp outside the allowed window")
    try:
        key = load_der_public_key(base64.b64decode(public_key_b64))
        if not isinstance(key, ec.EllipticCurvePublicKey):
            raise SignatureError("configured key is not an elliptic-curve public key")
        key.verify(base64.b64decode(signature_b64), timestamp.encode("utf-8") + body, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        raise SignatureError("signature does not match") from None
    except (binascii.Error, ValueError):
        raise SignatureError("malformed signature or key") from None


def is_bot_user_agent(user_agent: str | None) -> bool:
    ua = (user_agent or "").lower()
    return any(marker in ua for marker in config.CLICK_BOT_UA_MARKERS)


def reduce_events(body: bytes) -> list[dict]:
    """Click events that carry one of our run ids, reduced to {run_id, url, timestamp, bot}.

    Everything else in the batch (other event types, other senders' mail, malformed items) is ignored.
    Raises ValueError if the body is not a JSON array.
    """
    events = json.loads(body)
    if not isinstance(events, list):
        raise ValueError("webhook body is not a JSON array")
    out = []
    for ev in events:
        if not isinstance(ev, dict) or ev.get("event") != "click":
            continue
        run_id, url = ev.get("run_id"), ev.get("url")
        if not (isinstance(run_id, str) and 0 < len(run_id) <= _MAX_RUN_ID):
            continue
        if not (isinstance(url, str) and 0 < len(url) <= _MAX_URL):
            continue
        ts = ev.get("timestamp")
        out.append({"run_id": run_id, "url": url,
                    "timestamp": ts if isinstance(ts, int) and not isinstance(ts, bool) else None,
                    "bot": is_bot_user_agent(ev.get("useragent"))})
    return out
