"""bgg_hot (board games): ships disabled, never in premium, parses the documented XML shape (synthetic fixture)."""
import pytest

import newsletter_def
from widget_fakes import FakeResponse, ctx, fixture_text
from widgets import registry
from widgets.bgg_hot import parse_hot
from widgets.runner import produce_edition_widgets

W = registry.get("bgg_hot")


def test_no_token_means_skipped(monkeypatch):
    monkeypatch.delenv("BGG_API_TOKEN", raising=False)
    res = produce_edition_widgets(ctx({}), [{"id": "bgg_hot"}])
    assert res.summary() == {"bgg_hot": "skipped_no_key"}
    assert "BGG_API_TOKEN" in res.status["bgg_hot"]["error"]


def test_never_in_premium(monkeypatch):
    monkeypatch.setenv("BGG_API_TOKEN", "t")
    assert "bgg_hot" not in {w["id"] for w in registry.catalog(for_custom=True)}
    raw = {"version": 1, "name": "Mine", "accent_color": "#112233",
           "blocks": [{"type": "widget", "widget": "bgg_hot", "topic": "boardgames"}]}
    with pytest.raises(newsletter_def.DefinitionError) as e:
        newsletter_def.parse(raw, widgets=registry.definitions_catalog(), custom=True)
    assert "cannot be used in a custom newsletter" in str(e.value)
    # a free ready-made newsletter (custom=False) may place it
    newsletter_def.parse(raw, widgets=registry.definitions_catalog(), custom=False)


def test_with_token_fixture_render(monkeypatch):
    monkeypatch.setenv("BGG_API_TOKEN", "secret-token")
    c = ctx({"xmlapi2/hot": FakeResponse(200, text=fixture_text("bgg_hot_synthetic.xml"))})
    data = W.produce(c, {})
    assert c.http.calls[0][1]["headers"] == {"Authorization": "Bearer secret-token"}
    assert [r["name"] for r in data["rows"]] == ["Example Game One", "Example & Game Two", "Example Game Three"]
    assert data["rows"][2]["year"] is None
    html = W.render(data, {}, None)
    assert "Example &amp; Game Two" in html and 'href="https://boardgamegeek.com/boardgame/100001"' in html
    assert "secret-token" not in html


def test_entities_and_bad_items_are_refused():
    with pytest.raises(ValueError):
        parse_hot('<!DOCTYPE x [<!ENTITY a "aaaa">]><items><item id="1"><name value="&a;"/></item></items>')
    assert parse_hot('<items><item id="x"><name value="n"/></item><item id="2"/></items>') == []
