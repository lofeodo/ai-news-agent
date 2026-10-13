"""The confirm and unsubscribe links open a page with a button; the button's POST does the work."""
import test_subscription_tokens as base


api = base.api


def test_opening_the_confirm_link_changes_nothing(api, monkeypatch):
    monkeypatch.setattr(base.subs, "_active_subscriber_count", lambda db: 0)
    api.store["a@example.com"] = base._sub(active=False, expires=base.FUTURE)
    r = api.client.get("/confirm", params={"token": "tok"})
    assert r.status_code == 200 and 'method="post"' in r.text and "Confirm subscription" in r.text
    assert api.store["a@example.com"]["active"] is False


def test_opening_the_unsubscribe_link_changes_nothing(api):
    api.store["a@example.com"] = base._sub(active=True, expires=base.FUTURE)
    r = api.client.get("/unsubscribe", params={"token": "tok"})
    assert r.status_code == 200 and 'method="post"' in r.text
    assert api.store["a@example.com"]["active"] is True


def test_button_press_acts_and_repeats_are_fine(api, monkeypatch):
    monkeypatch.setattr(base.subs, "_active_subscriber_count", lambda db: 0)
    api.store["a@example.com"] = base._sub(active=False, expires=base.FUTURE)
    for _ in range(2):
        assert api.client.post("/confirm", params={"token": "tok"}).status_code == 200
    assert api.store["a@example.com"]["active"] is True
    for _ in range(2):
        assert api.client.post("/unsubscribe", params={"token": "tok"}).status_code == 200
    assert api.store["a@example.com"]["active"] is False


def test_unknown_or_malformed_token_gets_the_invalid_page(api):
    for method in (api.client.get, api.client.post):
        for path in ("/confirm", "/unsubscribe"):
            assert method(path, params={"token": "nope"}).status_code == 404
            assert method(path, params={"token": "bad token!"}).status_code == 404
