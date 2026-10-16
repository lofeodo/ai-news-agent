"""Unsubscribe and preferences per newsletter: emailed links carry `n`; a link without it means the AI newsletter."""
from datetime import datetime, timedelta, timezone

import subscriptions_model as sm
from subs_api import ADMIN, AS_ADMIN, AS_READER, READER, make_api

NOW = datetime.now(timezone.utc)
PAST = NOW - timedelta(days=1)


def _doc(email="a@example.com", newsletters=("ai", "finance"), token="tok", expires=NOW + timedelta(days=300), **extra):
    ids = list(newsletters)
    return {"email": email, "token": token, "token_expires_at": expires, "active": "ai" in ids,
            "newsletters": ids, "subscribed_any": bool(ids), "prefs": {"include_french": True}, **extra}


# --- unsubscribe ---------------------------------------------------------------------------------------

def test_an_old_link_without_n_unsubscribes_from_ai_only(monkeypatch):
    api = make_api(monkeypatch, {"a@example.com": _doc()})
    r = api.client.post("/unsubscribe", params={"token": "tok"})
    assert r.status_code == 200 and "Markets &amp; Money Weekly" in r.text
    doc = api.subs["a@example.com"]
    assert doc["active"] is False and sm.subscribed_ids(doc) == ["finance"] and doc["subscribed_any"] is True


def test_a_topic_link_unsubscribes_from_that_topic_only(monkeypatch):
    api = make_api(monkeypatch, {"a@example.com": _doc()})
    api.client.post("/unsubscribe", params={"token": "tok", "n": "finance"})
    doc = api.subs["a@example.com"]
    assert sm.subscribed_ids(doc) == ["ai"] and doc["active"] is True
    assert doc["newsletter_meta"] == {}           # nothing recorded for finance before, nothing invented


def test_unsubscribe_from_everything(monkeypatch):
    api = make_api(monkeypatch, {"a@example.com": _doc(newsletters=("ai", "finance", "gaming"),
                                                       pending_newsletters=["boardgames"])})
    r = api.client.post("/unsubscribe", params={"token": "tok", "n": "finance", "all": "1"})
    assert "any of our newsletters" in r.text
    doc = api.subs["a@example.com"]
    assert sm.subscribed_ids(doc) == [] and doc["subscribed_any"] is False and "pending_newsletters" not in doc


def test_the_page_names_the_newsletter_and_offers_everything(monkeypatch):
    api = make_api(monkeypatch, {"a@example.com": _doc()})
    r = api.client.get("/unsubscribe", params={"token": "tok", "n": "finance"})
    assert "stop receiving Markets &amp; Money Weekly" in r.text and "Latent SpaceMail" in r.text
    assert "Unsubscribe from everything" in r.text and "all=1" in r.text and "n=finance" in r.text
    assert sm.subscribed_ids(api.subs["a@example.com"]) == ["ai", "finance"]   # opening changes nothing


def test_an_ai_only_reader_sees_the_page_as_before(monkeypatch):
    api = make_api(monkeypatch, {"a@example.com": _doc(newsletters=("ai",))})
    r = api.client.get("/unsubscribe", params={"token": "tok"})
    assert "Press the button to stop receiving Latent SpaceMail. You can re-subscribe any time." in r.text
    assert "everything" not in r.text and r.text.count('method="post"') == 1
    r = api.client.post("/unsubscribe", params={"token": "tok"})
    assert "Sorry to see you go" in r.text


def test_unknown_newsletter_in_a_link_is_the_invalid_page(monkeypatch):
    api = make_api(monkeypatch, {"a@example.com": _doc()})
    for method in (api.client.get, api.client.post):
        assert method("/unsubscribe", params={"token": "tok", "n": "nope"}).status_code == 404
    assert sm.subscribed_ids(api.subs["a@example.com"]) == ["ai", "finance"]


def test_a_topic_only_readers_link_works_after_the_expiry_date(monkeypatch):
    """Like an AI reader's: expiry dates only bind pending sign-ups."""
    api = make_api(monkeypatch, {"f@example.com": _doc("f@example.com", newsletters=("finance",), expires=PAST)})
    assert api.client.get("/unsubscribe", params={"token": "tok", "n": "finance"}).status_code == 200
    api.client.post("/unsubscribe", params={"token": "tok", "n": "finance"})
    assert sm.subscribed_ids(api.subs["f@example.com"]) == []


# --- preferences by emailed link ------------------------------------------------------------------------

def test_preferences_list_the_newsletters(monkeypatch):
    api = make_api(monkeypatch, {"a@example.com": _doc()}, public_topics="gaming")
    body = api.client.get("/preferences", params={"token": "tok"}).json()
    assert body["newsletters"] == ["ai", "finance"] and body["prefs"] == {"include_french": True}
    available = {n["id"]: n for n in body["available"]}
    assert set(available) == {"ai", "finance", "gaming"}       # hidden finance shows because it is subscribed
    assert available["finance"]["hidden"] is True and available["gaming"]["hidden"] is False


def test_preferences_add_and_remove_are_instant(monkeypatch):
    api = make_api(monkeypatch, {"a@example.com": _doc()}, public_topics="gaming")
    r = api.client.post("/preferences", json={"token": "tok", "newsletters": ["ai", "gaming"],
                                              "consent_text_version": sm.CONSENT_CURRENT})
    assert r.json() == {"status": "ok"}
    doc = api.subs["a@example.com"]
    assert sm.subscribed_ids(doc) == ["ai", "gaming"] and doc["prefs"] == {"include_french": True}
    assert doc["newsletter_meta"]["gaming"]["consent_text_version"] == sm.CONSENT_CURRENT
    api.client.post("/preferences", json={"token": "tok", "newsletters": []})
    assert sm.subscribed_ids(api.subs["a@example.com"]) == [] and api.subs["a@example.com"]["active"] is False


def test_preferences_cannot_add_a_hidden_topic(monkeypatch):
    api = make_api(monkeypatch, {"a@example.com": _doc(newsletters=("ai",))})
    r = api.client.post("/preferences", json={"token": "tok", "newsletters": ["ai", "boardgames"]})
    assert r.status_code == 404 and sm.subscribed_ids(api.subs["a@example.com"]) == ["ai"]


def test_ai_prefs_alone_still_work(monkeypatch):
    api = make_api(monkeypatch, {"a@example.com": _doc()})
    api.client.post("/preferences", json={"token": "tok", "prefs": {"include_french": False, "include_canada": True}})
    doc = api.subs["a@example.com"]
    assert doc["prefs"] == {"include_french": False, "include_canada": True}
    assert sm.subscribed_ids(doc) == ["ai", "finance"]


def test_a_pending_sign_up_link_adding_a_newsletter_confirms_it(monkeypatch):
    api = make_api(monkeypatch, {"p@example.com": _doc("p@example.com", newsletters=(),
                                                       expires=NOW + timedelta(hours=40),
                                                       expires_at=NOW + timedelta(days=6))})
    api.client.post("/preferences", json={"token": "tok", "newsletters": ["ai"]})
    doc = api.subs["p@example.com"]
    assert doc["active"] is True and "expires_at" not in doc and doc["confirmed_at"]


# --- account ---------------------------------------------------------------------------------------------

def test_account_preferences_and_unsubscribe(monkeypatch):
    api = make_api(monkeypatch, {READER: _doc(READER)}, public_topics="finance,gaming")
    body = api.client.get("/auth/preferences", headers=AS_READER).json()
    assert body["subscribed"] is True and body["newsletters"] == ["ai", "finance"]
    assert {n["id"] for n in body["available"]} == {"ai", "finance", "gaming"}
    assert api.client.get("/auth/me", headers=AS_READER).json()["newsletters"] == ["ai", "finance"]

    api.client.post("/auth/preferences", json={"newsletters": ["finance", "gaming"]}, headers=AS_READER)
    assert sm.subscribed_ids(api.subs[READER]) == ["finance", "gaming"]
    api.client.post("/auth/unsubscribe", json={"newsletter": "gaming"}, headers=AS_READER)
    assert sm.subscribed_ids(api.subs[READER]) == ["finance"]
    api.client.post("/auth/unsubscribe", json={"all": True}, headers=AS_READER)
    assert sm.subscribed_ids(api.subs[READER]) == []


def test_account_unsubscribe_without_a_body_is_ai(monkeypatch):
    api = make_api(monkeypatch, {READER: _doc(READER)})
    api.client.post("/auth/unsubscribe", headers=AS_READER)
    assert sm.subscribed_ids(api.subs[READER]) == ["finance"] and api.subs[READER]["active"] is False


def test_admin_account_preferences_may_add_a_hidden_topic(monkeypatch):
    api = make_api(monkeypatch, {ADMIN: _doc(ADMIN, newsletters=("ai",))})
    assert {n["id"] for n in api.client.get("/auth/preferences", headers=AS_ADMIN).json()["available"]} == {
        "ai", "finance", "gaming", "boardgames", "printing3d"}
    api.client.post("/auth/preferences", json={"newsletters": ["ai", "printing3d"]}, headers=AS_ADMIN)
    assert sm.subscribed_ids(api.subs[ADMIN]) == ["ai", "printing3d"]
