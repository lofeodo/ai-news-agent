"""most_played (video games) on the recorded Steam responses."""
import requests

from widget_fakes import FakeResponse, ctx, fixture_json
from widgets import registry
from widgets.runner import produce_edition_widgets

W = registry.get("most_played")


def _app_route(url, kw):
    appid = url.split("appids=")[1].split("&")[0]
    rec = fixture_json("steam_appdetails.json")
    return FakeResponse(200, {appid: rec[appid]} if appid in rec else {appid: {"success": False}})


def _routes(**over):
    return {**over, "GetMostPlayedGames": FakeResponse(200, fixture_json("steam_most_played.json")),
            "appdetails": _app_route}


def test_fixture_render():
    data = W.produce(ctx(_routes()), {})
    assert len(data["rows"]) == 10 and data["rows"][0]["name"] == "Counter-Strike 2"
    assert data["as_of"].startswith("2026-10-15")
    html = W.render(data, {}, None)
    assert "Counter-Strike 2" in html and "1,165,887" in html
    assert 'href="https://store.steampowered.com/app/730"' in html
    deadlock = next(r for r in data["rows"] if r["name"] == "Deadlock")   # rank 5, was 6
    assert (deadlock["rank"], deadlock["last_week_rank"]) == (5, 6)
    assert "&#9650;&nbsp;+1" in html


def test_not_commercial():
    assert W.commercial_use is False
    assert "most_played" not in {w["id"] for w in registry.catalog(for_custom=True)}


def test_name_lookup_failures_drop_rows():
    def flaky(url, kw):
        if "appids=730" in url:
            raise requests.Timeout("slow")
        return _app_route(url, kw)
    data = W.produce(ctx({**_routes(), "appdetails": flaky}), {})
    names = [r["name"] for r in data["rows"]]
    # the recording names only the top 10, so the 11th and 12th cannot fill in here
    assert "Counter-Strike 2" not in names and len(names) == 9


def test_chart_errors_fail_and_old_chart_is_stale():
    res = produce_edition_widgets(ctx({"GetMostPlayedGames": FakeResponse(200, {"response": {}})}),
                                  [{"id": "most_played"}])
    assert res.summary() == {"most_played": "failed"}
    old = fixture_json("steam_most_played.json")
    old["response"]["rollup_date"] = 1780000000   # May 2026
    res = produce_edition_widgets(ctx({"GetMostPlayedGames": FakeResponse(200, old), "appdetails": _app_route}),
                                  [{"id": "most_played"}])
    assert res.summary() == {"most_played": "stale"}
