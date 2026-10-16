"""agents/subscriptions_model.py: one subscriber doc, many newsletters, with the legacy AI flag kept in step."""
from datetime import datetime, timezone

import subscriptions_model as sm

T = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def test_legacy_docs_read_as_ai_or_nothing():
    assert sm.subscribed_ids({"active": True}) == ["ai"]
    assert sm.subscribed_ids({"active": False}) == []
    assert sm.subscribed_ids({}) == []
    assert sm.subscribed_ids(None) == []


def test_active_is_the_truth_for_ai():
    """A doc changed by code that only knows `active` still means what agent4 will send."""
    assert sm.subscribed_ids({"active": False, "newsletters": ["ai", "finance"]}) == ["finance"]
    assert sm.subscribed_ids({"active": True, "newsletters": ["finance"]}) == ["ai", "finance"]


def test_ids_are_unique_and_ai_first():
    doc = {"active": True, "newsletters": ["gaming", "ai", "gaming", "", None, 3]}
    assert sm.subscribed_ids(doc) == ["ai", "gaming"]
    assert sm.is_subscribed(doc, "gaming") and not sm.is_subscribed(doc, "finance")


def test_add_to_a_legacy_ai_doc_keeps_ai():
    f = sm.add({"active": True}, "finance", sm.CONSENT_CURRENT, now=T)
    assert f["newsletters"] == ["ai", "finance"]
    assert f["active"] is True and f["subscribed_any"] is True
    assert f["newsletter_meta"] == {"finance": {"subscribed_at": T, "consent_text_version": sm.CONSENT_CURRENT}}


def test_add_ai_sets_the_legacy_flag():
    f = sm.add({"active": False, "newsletters": ["finance"]}, "ai", sm.CONSENT_LEGACY, now=T)
    assert f["newsletters"] == ["ai", "finance"] and f["active"] is True


def test_add_to_a_new_doc():
    f = sm.add(None, "gaming", sm.CONSENT_CURRENT, now=T)
    assert f == {"newsletters": ["gaming"], "active": False, "subscribed_any": True,
                 "newsletter_meta": {"gaming": {"subscribed_at": T, "consent_text_version": sm.CONSENT_CURRENT}}}


def test_add_twice_is_one_subscription_with_the_newest_consent():
    doc = {"active": False, **sm.add(None, "gaming", sm.CONSENT_LEGACY, now=T)}
    later = datetime(2026, 11, 1, tzinfo=timezone.utc)
    f = sm.add(doc, "gaming", sm.CONSENT_CURRENT, now=later)
    assert f["newsletters"] == ["gaming"]
    assert f["newsletter_meta"]["gaming"] == {"subscribed_at": later, "consent_text_version": sm.CONSENT_CURRENT}


def test_remove_ai_clears_the_flag_and_keeps_the_others():
    doc = {"active": True, "newsletters": ["ai", "finance"]}
    f = sm.remove(doc, "ai", now=T)
    assert f["newsletters"] == ["finance"] and f["active"] is False and f["subscribed_any"] is True


def test_remove_the_last_one_clears_subscribed_any():
    doc = {"active": False, **sm.add(None, "finance", sm.CONSENT_CURRENT, now=T)}
    f = sm.remove(doc, "finance", now=T)
    assert f["newsletters"] == [] and f["subscribed_any"] is False and f["active"] is False
    assert f["newsletter_meta"]["finance"]["unsubscribed_at"] == T          # the consent record stays
    assert f["newsletter_meta"]["finance"]["consent_text_version"] == sm.CONSENT_CURRENT


def test_remove_from_a_legacy_doc():
    assert sm.remove({"active": True}, "ai", now=T) == {
        "newsletters": [], "newsletter_meta": {}, "active": False, "subscribed_any": False}
    assert sm.remove({"active": True}, "finance", now=T)["active"] is True   # not subscribed: AI untouched


def test_remove_all():
    doc = {"active": True, "newsletters": ["ai", "finance", "gaming"],
           "newsletter_meta": {"finance": {"subscribed_at": T, "consent_text_version": "x"}}}
    f = sm.remove_all(doc, now=T)
    assert f["newsletters"] == [] and f["active"] is False and f["subscribed_any"] is False
    assert f["newsletter_meta"]["finance"]["unsubscribed_at"] == T


def test_consent_version_falls_back_to_legacy():
    assert sm.consent_version(sm.CONSENT_CURRENT) == sm.CONSENT_CURRENT
    assert sm.consent_version(None) == sm.CONSENT_LEGACY
    assert sm.consent_version("made-up") == sm.CONSENT_LEGACY
