"""A TestClient on the subscription router over the Firestore fake: mail captured, tokens faked, no network.

Bearer tokens: "admin" (an ADMIN_EMAILS address), "reader" (a verified reader), anything else is rejected.
"""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_subscriptions as subs
import auth_middleware
from fakes_firestore import FakeDb

ADMIN = "boss@example.com"
READER = "reader@example.com"
AS_ADMIN = {"Authorization": "Bearer admin"}
AS_READER = {"Authorization": "Bearer reader"}


def make_api(monkeypatch, subscribers=None, runs=None, public_topics=""):
    if public_topics:
        monkeypatch.setenv("PUBLIC_TOPICS", public_topics)
    else:
        monkeypatch.delenv("PUBLIC_TOPICS", raising=False)
    db = FakeDb({"subscribers": dict(subscribers or {}), "pipeline_runs": dict(runs or {})})
    mails = []

    def claim_latest(db_, ref):
        if (ref.get().to_dict() or {}).get("latest_sent"):
            return False
        ref.update({"latest_sent": True})
        return True

    def fake_verify(token, check_revoked=False):
        if token == "admin":
            return {"uid": "u-admin", "email": ADMIN, "email_verified": True}
        if token == "reader":
            return {"uid": "u-reader", "email": READER, "email_verified": True}
        raise ValueError("bad token")

    import firebase_admin.auth as fb_auth
    monkeypatch.setattr(fb_auth, "verify_id_token", fake_verify)
    monkeypatch.setattr(auth_middleware, "_ensure_firebase", lambda: None)
    monkeypatch.setattr(subs, "ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(subs, "_db", lambda: db)
    monkeypatch.setattr(subs, "_claim_latest", claim_latest)
    monkeypatch.setattr(subs, "_send_email", lambda to, subject, html: mails.append(
        SimpleNamespace(to=to, subject=subject, html=html)))
    monkeypatch.setattr(subs.mail_throttle, "allow_mail", lambda db_, email: True)
    monkeypatch.setattr(subs.time, "sleep", lambda s: None)
    monkeypatch.setattr(subs.limiter, "enabled", False)
    monkeypatch.setitem(subs._LATEST_ISSUES_CACHE, "value", None)
    monkeypatch.setattr(subs, "SERVICE_BASE_URL", "https://api.test")
    monkeypatch.setattr(subs, "FRONTEND_BASE_URL", "https://site.test")
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    return SimpleNamespace(client=TestClient(app), db=db, subs=db.store["subscribers"],
                           runs=db.store["pipeline_runs"], mails=mails)


def issue(started, topic=None, sent=True, kind="release", html="<p>{{UNSUBSCRIBE_URL}} {{PREFERENCES_URL}}</p>"):
    """A composed run doc as agent3 leaves it (sent = agent4 already mailed it)."""
    doc = {"started_at": started, "run_kind": kind, "newsletter_composed": True,
           "newsletter_subject": f"Issue {started}", "newsletter_variants": {"0_0": html}}
    if topic:
        doc["topic"] = topic
    if sent:
        doc["agent4_send_summary"] = {"sent": 1, "total": 1}
    return doc
