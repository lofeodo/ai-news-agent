"""free_games (video games) on the recorded Epic and GamerPower responses."""
import requests

from widget_fakes import NOW, FakeResponse, ctx, fixture_json
from widgets import registry
from widgets.free_games import parse_epic, parse_gamerpower

W = registry.get("free_games")
EPIC = "freeGamesPromotions"
GP = "gamerpower.com"


def test_epic_fixture():
    data = W.produce(ctx({EPIC: FakeResponse(200, fixture_json("epic_free_games.json"))}), {})
    assert data["source"] == "epic"
    assert [g["title"] for g in data["now"]] == ["Bad Cheese", "Agent A: A puzzle in disguise"]
    assert data["now"][0]["until"].startswith("2026-10-22T15:00")
    assert data["now"][0]["url"] == "https://store.epicgames.com/en-US/p/bad-cheese-4256d9"
    assert {g["title"] for g in data["upcoming"]} == {"Sker Ritual", "Home - A Unique Horror Adventure"}
    # 20%-off sales are not "free"
    assert "Eternal Threads" not in str(data)
    html = W.render(data, {}, None)
    assert "free until Oct 22" in html and "free from Oct 22 to Oct 29" in html and "Epic Games Store" in html


def test_gamerpower_fallback_when_epic_fails():
    session_routes = {EPIC: requests.ConnectionError("down"), GP: FakeResponse(200, fixture_json("gamerpower.json"))}
    data = W.produce(ctx(session_routes), {})
    assert data["source"] == "gamerpower" and data["now"][0]["title"] == "Best Plumber (IndieGala)"
    html = W.render(data, {}, None)
    assert 'href="https://www.gamerpower.com"' in html   # the attribution link GamerPower requires


def test_changed_epic_payload_falls_back():
    for broken in ({"data": {"Catalog": None}}, {"data": {"Catalog": {"searchStore": {"elements": "x"}}}}, ["x"]):
        data = W.produce(ctx({EPIC: FakeResponse(200, broken), GP: FakeResponse(200, fixture_json("gamerpower.json"))}), {})
        assert data["source"] == "gamerpower"


def test_epic_elements_with_odd_shapes_are_skipped():
    payload = {"data": {"Catalog": {"searchStore": {"elements": [
        None, {"title": 3}, {"title": "No promos", "promotions": None},
        {"title": "Weird", "promotions": {"promotionalOffers": [{"promotionalOffers": [{"discountSetting": None}]}]}},
        {"title": "Bad dates", "promotions": {"promotionalOffers": [{"promotionalOffers": [
            {"startDate": "soon", "endDate": "later", "discountSetting": {"discountPercentage": 0}}]}]}},
    ]}}}}
    assert parse_epic(payload, NOW) == {"source": "epic", "now": [], "upcoming": []}


def test_gamerpower_skips_expired_and_inactive():
    items = [{"title": "Old Giveaway", "end_date": "2020-01-01 00:00:00"},
             {"title": "Gone", "status": "Expired"},
             {"title": "Open Giveaway", "end_date": "N/A", "open_giveaway_url": "https://www.gamerpower.com/open/x"}]
    assert [g["title"] for g in parse_gamerpower(items, NOW)["now"]] == ["Open"]


def test_nothing_free_anywhere_is_empty():
    from widgets.runner import produce_edition_widgets
    res = produce_edition_widgets(ctx({EPIC: FakeResponse(200, {"data": {"Catalog": {"searchStore": {"elements": []}}}}),
                                       GP: FakeResponse(200, [])}), [{"id": "free_games"}])
    assert res.summary() == {"free_games": "empty"}
