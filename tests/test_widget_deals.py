"""deals (video games) on the recorded CheapShark responses."""
import requests

from widget_fakes import FakeResponse, ctx, fixture_json
from widgets import registry

W = registry.get("deals")


def _routes(**over):
    return {**over, "/deals": FakeResponse(200, fixture_json("cheapshark_deals.json")),
            "/stores": FakeResponse(200, fixture_json("cheapshark_stores.json"))}


def test_fixture_render_links_through_cheapshark():
    data = W.produce(ctx(_routes()), {})
    first = data["rows"][0]
    assert first["title"].startswith("The Witcher 3") and first["sale"] == 24.99 and first["savings"] == 50
    assert first["store"]
    html = W.render(data, {}, None)
    assert html.count('href="https://www.cheapshark.com/redirect?dealID=') == len(data["rows"])
    assert "dealID=OR%2BVz0QIAULaNU5tjUdvoTs3iqGm6lodrS%2BXzXvxPXg%3D" in html
    assert "$24.99" in html and "−50%" in html


def test_stores_failure_still_renders_without_store_names():
    data = W.produce(ctx({"/deals": FakeResponse(200, fixture_json("cheapshark_deals.json")),
                          "/stores": requests.ConnectionError("x")}), {})
    assert data["rows"] and all(r["store"] == "" for r in data["rows"])


def test_bad_rows_are_skipped():
    deals = [{"title": "No id"}, {"title": "Not cheaper", "dealID": "a", "salePrice": "10", "normalPrice": "10"},
             {"title": "Junk price", "dealID": "b", "salePrice": "x", "normalPrice": "10"},
             {"title": "Good", "dealID": "c&d=\"e\"", "salePrice": "5", "normalPrice": "10", "savings": "50",
              "storeID": "1"}]
    data = W.produce(ctx({"/deals": FakeResponse(200, deals), "/stores": FakeResponse(200, [])}), {})
    assert [r["title"] for r in data["rows"]] == ["Good"]
    html = W.render(data, {}, None)
    assert "dealID=c%26d%3D%22e%22" in html
