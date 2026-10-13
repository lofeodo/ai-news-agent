"""Request handling in the subscription service: blocking work runs off the event loop, mail calls time out."""
import inspect

import agent_subscriptions as subs
import auth_middleware

SYNC_HANDLERS = [
    "auth_me", "auth_unsubscribe", "auth_google_exchange", "auth_get_preferences",
    "auth_update_preferences", "auth_sections_refine", "auth_get_sections",
    "auth_update_sections", "admin_preview",
]


def test_blocking_handlers_are_plain_functions():
    for name in SYNC_HANDLERS:
        assert not inspect.iscoroutinefunction(getattr(subs, name)), name
    assert not inspect.iscoroutinefunction(auth_middleware.get_current_user)


def test_account_subscribe_hands_work_to_a_thread():
    assert inspect.iscoroutinefunction(subs.auth_subscribe)
    assert not inspect.iscoroutinefunction(subs._subscribe_account)
