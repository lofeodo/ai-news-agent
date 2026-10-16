"""market_snapshot (finance) on the recorded Bank of Canada and US Treasury responses."""
import requests

from widget_fakes import FakeResponse, ctx, fixture_json, fixture_text
from widgets import registry
from widgets.runner import produce_edition_widgets

W = registry.get("market_snapshot")


def _routes(**over):
    return {"bankofcanada.ca/valet": FakeResponse(200, fixture_json("boc_market.json")),
            "home.treasury.gov": FakeResponse(200, text=fixture_text("treasury_yields.csv")), **over}


def test_fixture_render():
    data = W.produce(ctx(_routes()), {})
    by = {r["key"]: r for r in data["rows"]}
    assert set(by) == {"usdcad", "eurcad", "ca2y", "ca10y", "us2y", "us10y"}
    assert by["usdcad"]["value"] == 1.4196 and by["usdcad"]["date"] == "2026-10-15"
    assert by["us10y"]["value"] == 5.10 and len(by["us10y"]["spark"]) == 5
    assert by["us10y"]["spark"][-1] == 5.10
    # 10-year on Oct 15 (5.10) vs the last close on or before Oct 11 (Oct 9: 5.24)
    assert round(by["us10y"]["change"], 2) == -0.14
    html = W.render(data, {}, None)
    assert "1.4196" in html and "5.10%" in html and "&#9660;&nbsp;−14 bp" in html
    assert "Bank of Canada" in html and "US Treasury" in html and "not financial advice" in html
    assert 'role="img" aria-label="last five days"' in html


def test_missing_series_drops_one_row():
    boc = fixture_json("boc_market.json")
    for obs in boc["observations"]:
        obs.pop("FXEURCAD", None)
    data = W.produce(ctx(_routes(**{"bankofcanada.ca/valet": FakeResponse(200, boc)})), {})
    assert "eurcad" not in {r["key"] for r in data["rows"]} and len(data["rows"]) == 5


def test_one_source_down_keeps_the_other():
    data = W.produce(ctx(_routes(**{"home.treasury.gov": requests.ConnectionError("x")})), {})
    assert {r["source"] for r in data["rows"]} == {"boc"}
    assert "US Treasury" not in W.render(data, {}, None)


def test_old_series_are_dropped_and_nothing_left_fails():
    res = produce_edition_widgets(ctx(_routes(), week="2026-11-30"), [{"id": "market_snapshot"}])
    assert res.summary() == {"market_snapshot": "failed"}


def test_treasury_csv_with_junk_rows():
    csv = 'Date,"2 Yr","10 Yr"\nnot a date,1,2\n10/15/2026,N/A,5.10\n10/14/2026,4.82,5.19\n'
    data = W.produce(ctx(_routes(**{"home.treasury.gov": FakeResponse(200, text=csv)})), {})
    by = {r["key"]: r for r in data["rows"]}
    assert by["us2y"]["value"] == 4.82 and by["us10y"]["value"] == 5.10
