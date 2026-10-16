"""week_ahead (finance) from the dated calendar; no network."""
from widget_fakes import ctx
from widgets import registry
from widgets.runner import produce_edition_widgets

W = registry.get("week_ahead")


def test_busy_week():
    data = W.produce(ctx({}, week="2026-10-26"), {})
    assert data["quiet"] is False
    assert [(e["date"], e["name"].split()[0]) for e in data["events"]] == [
        ("2026-10-28", "Bank"), ("2026-10-28", "Federal"), ("2026-10-29", "European")]
    html = W.render(data, {}, None)
    assert "Wed Oct 28 · 9:45 AM ET" in html and "Thu Oct 29" in html and "not financial advice" in html


def test_quiet_week_shows_what_comes_next():
    data = W.produce(ctx({}, week="2026-10-19"), {})
    assert data["quiet"] is True and len(data["events"]) == 3 and data["events"][0]["date"] == "2026-10-28"
    assert "quiet week" in W.render(data, {}, None)


def test_week_boundaries_are_monday_to_sunday():
    data = W.produce(ctx({}, week="2026-11-02"), {})   # jobs report Fri Nov 6, CPI Tue Nov 10 is next week
    assert [e["date"] for e in data["events"]] == ["2026-11-06"]


def test_expired_calendar_fails():
    res = produce_edition_widgets(ctx({}, week="2028-01-03"), [{"id": "week_ahead"}])
    assert res.summary() == {"week_ahead": "failed"}
