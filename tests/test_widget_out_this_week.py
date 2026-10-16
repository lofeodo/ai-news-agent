"""out_this_week (video games, RAWG): ships disabled, never leaks its key, parses the documented shape."""
import requests

from widget_fakes import FakeResponse, ctx, fixture_json
from widgets import registry
from widgets.runner import produce_edition_widgets

W = registry.get("out_this_week")


def test_no_key_means_skipped(monkeypatch):
    monkeypatch.delenv("RAWG_API_KEY", raising=False)
    assert produce_edition_widgets(ctx({}), [{"id": "out_this_week"}]).summary() == {"out_this_week": "skipped_no_key"}
    assert W.commercial_use is False


def test_fixture_render(monkeypatch):
    monkeypatch.setenv("RAWG_API_KEY", "k123")
    c = ctx({"api.rawg.io": FakeResponse(200, fixture_json("rawg_games_synthetic.json"))}, week="2026-10-19")
    data = W.produce(c, {})
    params = c.http.calls[0][1]["params"]
    assert params["dates"] == "2026-10-19,2026-10-25" and params["key"] == "k123"
    assert [r["name"] for r in data["rows"]] == ["Example Release One", "Example <Release> Two"]   # no date: dropped
    html = W.render(data, {}, None)
    assert "Example &lt;Release&gt; Two" in html and "PC, PlayStation 5" in html
    assert 'href="https://rawg.io/games/example-release-one"' in html and 'href="https://rawg.io"' in html


def test_errors_never_carry_the_key(monkeypatch):
    monkeypatch.setenv("RAWG_API_KEY", "SECRET-KEY")
    err = requests.HTTPError("401 Client Error: Unauthorized for url: https://api.rawg.io/api/games?key=SECRET-KEY",
                             response=FakeResponse(401, {}))
    res = produce_edition_widgets(ctx({"api.rawg.io": err}), [{"id": "out_this_week"}])
    assert res.summary() == {"out_this_week": "failed"}
    assert "SECRET-KEY" not in str(res.status) and "HTTP 401" in res.status["out_this_week"]["error"]
