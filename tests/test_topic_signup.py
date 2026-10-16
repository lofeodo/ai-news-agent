"""Signing up for one or more newsletters: the AI form unchanged, topics behind the launch switch, additions confirmed."""
from datetime import datetime, timedelta, timezone

import pytest

import agent_subscriptions as subs
import subscriptions_model as sm
from subs_api import ADMIN, AS_ADMIN, AS_READER, READER, issue, make_api

NOW = datetime.now(timezone.utc)


def _ai_sub(email="a@example.com", token="tok", **extra):
    return {"email": email, "token": token, "active": True, "confirmed_at": NOW - timedelta(days=30),
            "token_expires_at": NOW + timedelta(days=300), "prefs": {}, **extra}


def _confirm(api, token):
    return api.client.post("/confirm", params={"token": token})


# --- the AI form, unchanged --------------------------------------------------------------------------

def test_ai_form_without_newsletters_is_the_ai_sign_up(monkeypatch):
    api = make_api(monkeypatch)
    assert api.client.post("/subscribe", json={"email": "New@Example.com"}).json() == {"status": "ok"}
    doc = api.subs["new@example.com"]
    assert doc["active"] is False and doc["pending_newsletters"] == ["ai"]
    assert api.mails[0].subject == "Confirm your subscription to Latent SpaceMail"
    assert "a weekly AI research &amp; news briefing" in api.mails[0].html

    r = _confirm(api, doc["token"])
    assert r.status_code == 200 and "You'll receive Latent SpaceMail every Monday" in r.text
    doc = api.subs["new@example.com"]
    assert doc["active"] is True and doc["newsletters"] == ["ai"] and doc["subscribed_any"] is True
    assert doc["newsletter_meta"]["ai"]["consent_text_version"] == sm.CONSENT_LEGACY
    assert not any(k.startswith("pending_") for k in doc) and "expires_at" not in doc


def test_a_pending_sign_up_from_before_the_deploy_confirms_as_ai(monkeypatch):
    """Its doc has no pending_newsletters field at all."""
    api = make_api(monkeypatch, {"old@example.com": {
        "email": "old@example.com", "token": "t-old", "active": False,
        "token_expires_at": NOW + timedelta(hours=20), "expires_at": NOW + timedelta(days=6)}})
    _confirm(api, "t-old")
    assert sm.subscribed_ids(api.subs["old@example.com"]) == ["ai"]


def test_already_subscribed_to_ai_gets_the_same_mail_as_before(monkeypatch):
    api = make_api(monkeypatch, {"a@example.com": _ai_sub()})
    api.client.post("/subscribe", json={"email": "a@example.com"})
    assert [m.subject for m in api.mails] == ["You're already subscribed to Latent SpaceMail"]
    assert "pending_newsletters" not in api.subs["a@example.com"]


# --- topics --------------------------------------------------------------------------------------------

def test_finance_sign_up_once_launched(monkeypatch):
    api = make_api(monkeypatch, public_topics="finance")
    api.client.post("/subscribe", json={"email": "f@example.com", "newsletters": ["finance"],
                                        "consent_text_version": sm.CONSENT_CURRENT})
    assert api.mails[0].subject == "Confirm your subscription to Markets & Money Weekly"
    assert "Markets &amp; Money Weekly" in api.mails[0].html
    r = _confirm(api, api.subs["f@example.com"]["token"])
    assert "Markets &amp; Money Weekly every Monday" in r.text
    doc = api.subs["f@example.com"]
    assert doc["newsletters"] == ["finance"] and doc["active"] is False and doc["subscribed_any"] is True
    assert doc["newsletter_meta"]["finance"]["consent_text_version"] == sm.CONSENT_CURRENT
    assert doc["confirmed_at"] and "expires_at" not in doc


def test_several_newsletters_in_one_form(monkeypatch):
    api = make_api(monkeypatch, public_topics="finance,gaming")
    api.client.post("/subscribe", json={"email": "m@example.com", "newsletters": ["gaming", "ai", "finance"]})
    assert api.mails[0].subject == ("Confirm your subscription to Latent SpaceMail, Markets & Money Weekly "
                                    "and Video Games Weekly")
    _confirm(api, api.subs["m@example.com"]["token"])
    assert sm.subscribed_ids(api.subs["m@example.com"]) == ["ai", "finance", "gaming"]


@pytest.mark.parametrize("headers", [{}, AS_READER, {"Authorization": "Bearer junk"}])
def test_hidden_or_unknown_topic_is_a_404(monkeypatch, headers):
    api = make_api(monkeypatch)
    for wanted in (["finance"], ["nope"], ["ai", "finance"]):
        r = api.client.post("/subscribe", json={"email": "x@example.com", "newsletters": wanted}, headers=headers)
        assert r.status_code == 404
    assert api.mails == [] and "x@example.com" not in api.subs


def test_an_admin_can_sign_up_for_a_hidden_topic(monkeypatch):
    api = make_api(monkeypatch)
    r = api.client.post("/subscribe", json={"email": ADMIN, "newsletters": ["boardgames"]}, headers=AS_ADMIN)
    assert r.status_code == 200 and api.subs[ADMIN]["pending_newsletters"] == ["boardgames"]


# --- adding a newsletter to an address that already gets one --------------------------------------------

def test_an_addition_needs_the_click(monkeypatch):
    api = make_api(monkeypatch, {"a@example.com": _ai_sub()}, public_topics="finance")
    api.client.post("/subscribe", json={"email": "a@example.com", "newsletters": ["ai", "finance"]})
    doc = api.subs["a@example.com"]
    assert sm.subscribed_ids(doc) == ["ai"] and doc["pending_newsletters"] == ["finance"]   # nothing added yet
    assert api.mails[0].subject == "Confirm your subscription to Markets & Money Weekly"
    assert "/confirm?token=tok" in api.mails[0].html and "add" in api.mails[0].html

    _confirm(api, "tok")
    doc = api.subs["a@example.com"]
    assert sm.subscribed_ids(doc) == ["ai", "finance"] and doc["token"] == "tok"
    assert doc["confirmed_at"] == NOW - timedelta(days=30)        # the first consent record is kept
    assert "pending_newsletters" not in doc


def test_a_stale_addition_is_not_applied(monkeypatch):
    api = make_api(monkeypatch, {"a@example.com": _ai_sub(
        pending_newsletters=["finance"], pending_requested_at=NOW - timedelta(days=3))}, public_topics="finance")
    r = _confirm(api, "tok")
    assert r.status_code == 200 and sm.subscribed_ids(api.subs["a@example.com"]) == ["ai"]


def test_already_subscribed_to_the_topic(monkeypatch):
    api = make_api(monkeypatch, {"f@example.com": {**_ai_sub("f@example.com"), "active": False,
                                                   "newsletters": ["finance"], "subscribed_any": True}},
                   public_topics="finance")
    api.client.post("/subscribe", json={"email": "f@example.com", "newsletters": ["finance"]})
    assert [m.subject for m in api.mails] == ["You're already subscribed to Markets & Money Weekly"]


# --- the cap counts people ----------------------------------------------------------------------------

def test_headcount_counts_people_not_subscriptions(monkeypatch):
    api = make_api(monkeypatch, {
        "a@example.com": _ai_sub("a@example.com", newsletters=["ai", "finance"], subscribed_any=True),
        "f@example.com": {"email": "f@example.com", "active": False, "newsletters": ["finance"], "subscribed_any": True},
        "gone@example.com": {"email": "gone@example.com", "active": False, "newsletters": [], "subscribed_any": False},
        "legacy@example.com": {"email": "legacy@example.com", "active": True},
    })
    assert subs._active_subscriber_count(api.db) == 3


def test_full_list_refuses_a_new_person_but_not_an_addition(monkeypatch):
    api = make_api(monkeypatch, {"a@example.com": _ai_sub()}, public_topics="finance")
    monkeypatch.setattr(subs, "MAX_SUBSCRIBERS", 1)
    assert api.client.post("/subscribe", json={"email": "new@example.com"}).status_code == 503
    assert api.client.post("/subscribe", json={"email": "a@example.com", "newsletters": ["finance"]}).status_code == 200
    _confirm(api, "tok")
    assert sm.subscribed_ids(api.subs["a@example.com"]) == ["ai", "finance"]


# --- the latest issue on confirm ----------------------------------------------------------------------

def test_send_latest_mails_that_newsletters_newest_sent_issue(monkeypatch):
    runs = {"ai-1": issue("2026-10-11T16:00:00Z"),
            "fin-1": issue("2026-10-11T14:00:00Z", topic="finance"),
            "fin-draft": issue("2026-10-18T14:00:00Z", topic="finance", sent=False)}
    api = make_api(monkeypatch, runs=runs, public_topics="finance,gaming")
    api.client.post("/subscribe", json={"email": "f@example.com", "newsletters": ["finance", "gaming"],
                                        "send_latest": True})
    token = api.subs["f@example.com"]["token"]
    api.mails.clear()
    _confirm(api, token)
    assert [m.subject for m in api.mails] == ["Markets & Money Weekly — last week's edition"]   # gaming has none
    assert f"https://api.test/unsubscribe?token={token}&amp;n=finance" not in api.mails[0].html
    assert f"https://api.test/unsubscribe?token={token}&n=finance" in api.mails[0].html
    assert f"https://site.test/preferences.html?token={token}&n=finance" in api.mails[0].html
    _confirm(api, token)
    assert len(api.mails) == 1


def test_ai_latest_issue_links_have_no_n(monkeypatch):
    api = make_api(monkeypatch, runs={"ai-1": issue("2026-10-11T16:00:00Z")})
    api.client.post("/subscribe", json={"email": "a@example.com", "send_latest": True})
    token = api.subs["a@example.com"]["token"]
    _confirm(api, token)
    assert api.mails[-1].subject == "Latent SpaceMail — last week's edition"
    assert f"https://api.test/unsubscribe?token={token} " in api.mails[-1].html


# --- account sign-up ------------------------------------------------------------------------------------

def test_account_subscribe_defaults_to_ai(monkeypatch):
    api = make_api(monkeypatch)
    assert api.client.post("/auth/subscribe", json={}, headers=AS_READER).json() == {"status": "ok"}
    doc = api.subs[READER]
    assert doc["active"] is True and doc["newsletters"] == ["ai"]
    assert doc["prefs"] == {"include_french": True, "include_canada": True}


def test_account_subscribe_to_a_topic(monkeypatch):
    api = make_api(monkeypatch, {READER: _ai_sub(READER, token="keep")}, public_topics="gaming")
    r = api.client.post("/auth/subscribe", json={"newsletter": "gaming", "consent_text_version": sm.CONSENT_CURRENT},
                        headers=AS_READER)
    assert r.json() == {"status": "ok"}
    doc = api.subs[READER]
    assert sm.subscribed_ids(doc) == ["ai", "gaming"] and doc["token"] == "keep"   # links already sent keep working
    assert doc["newsletter_meta"]["gaming"]["consent_text_version"] == sm.CONSENT_CURRENT
    r = api.client.post("/auth/subscribe", json={"newsletter": "gaming"}, headers=AS_READER)
    assert r.json() == {"status": "already_subscribed"}


def test_account_subscribe_hidden_topic(monkeypatch):
    api = make_api(monkeypatch)
    assert api.client.post("/auth/subscribe", json={"newsletter": "finance"}, headers=AS_READER).status_code == 404
    assert READER not in api.subs
    assert api.client.post("/auth/subscribe", json={"newsletter": "finance"}, headers=AS_ADMIN).status_code == 200
    assert api.subs[ADMIN]["newsletters"] == ["finance"] and api.subs[ADMIN]["active"] is False


def test_account_subscribe_sends_that_newsletters_latest(monkeypatch):
    api = make_api(monkeypatch, runs={"fin-1": issue("2026-10-11T14:00:00Z", topic="finance")},
                   public_topics="finance")
    api.client.post("/auth/subscribe", json={"newsletter": "finance", "send_latest": True}, headers=AS_READER)
    assert [m.subject for m in api.mails] == ["Markets & Money Weekly — last week's edition"]
