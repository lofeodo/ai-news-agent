"""rates_board (finance) on the recorded Bank of Canada, New York Fed and ECB responses."""
from datetime import date

import pytest
import requests

from widget_fakes import FakeResponse, ctx, fixture_json
from widgets import finance_data as fd
from widgets import registry
from widgets.runner import produce_edition_widgets

W = registry.get("rates_board")


def _routes(**over):
    return {"valet/observations/V39079": FakeResponse(200, fixture_json("boc_policy.json")),
            "markets.newyorkfed.org": FakeResponse(200, fixture_json("nyfed_effr.json")),
            "data-api.ecb.europa.eu": FakeResponse(200, fixture_json("ecb_dfr.json")), **over}


def test_fixture_render():
    data = W.produce(ctx(_routes()), {})
    by = {r["key"]: r for r in data["rows"]}
    assert by["boc"]["rate"] == 2.25 and by["boc"]["next"] == "2026-10-28" and by["boc"]["change_bp"] == 0
    assert (by["fomc"]["rate"], by["fomc"]["rate_hi"], by["fomc"]["next"]) == (3.75, 4.0, "2026-10-28")
    assert by["ecb"]["rate"] == 2.5 and by["ecb"]["next"] == "2026-10-29" and by["ecb"]["date"] == "2026-10-16"
    assert data["as_of"] == "2026-10-16"
    html = W.render(data, {}, None)
    assert "2.25%" in html and "3.75–4.00%" in html and "Oct 28" in html
    assert "not financial advice" in html and "newyorkfed.org" in html and "Bank of Canada" in html


def test_one_bank_down_is_one_missing_row():
    data = W.produce(ctx(_routes(**{"markets.newyorkfed.org": requests.ConnectionError("x")})), {})
    assert [r["key"] for r in data["rows"]] == ["boc", "ecb"]
    assert "newyorkfed.org" not in W.render(data, {}, None)


def test_all_banks_down_fails():
    bad = {"bankofcanada": FakeResponse(500, {}), "newyorkfed": FakeResponse(500, {}), "ecb.europa": FakeResponse(500, {})}
    assert produce_edition_widgets(ctx(bad), [{"id": "rates_board"}]).summary() == {"rates_board": "failed"}


def test_rate_change_is_shown_in_basis_points():
    points = [("2026-10-08", 2.5), ("2026-10-12", 2.5), ("2026-10-15", 2.25)]
    assert round(fd.change_over(points, 7, date(2026, 10, 18)) * 100) == -25
    assert fd.change_over(points[-1:], 7, date(2026, 10, 18)) is None
    sample = W.sample_data()
    assert "&#9660;&nbsp;−25 bp" in W.render(sample, {}, None)


def test_expired_calendar_fails_with_a_clear_message(monkeypatch):
    res = produce_edition_widgets(ctx(_routes(), week="2028-01-03"), [{"id": "rates_board"}])
    assert res.summary() == {"rates_board": "failed"}
    assert "add next year's dates" in res.status["rates_board"]["error"]


def test_calendar_file_is_consistent():
    cal = fd.load_calendar()
    assert cal["valid_until"] >= max(e["date"] for e in cal["events"])
    for e in cal["events"]:
        assert e["source"].startswith("https://") and e["date"].weekday() < 5, e
    keys = {e["key"] for e in cal["events"]}
    assert keys == {"boc", "fomc", "ecb", "us_cpi", "us_jobs"}
    with pytest.raises(fd.CalendarExpired):
        fd.check_calendar(cal, date(2028, 1, 1))
