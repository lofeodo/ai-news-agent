"""Rules every registered widget follows: metadata, sample render size and safety, never raising on odd data."""
import json

import pytest

import render
from render.palette import AI_LINK
from widgets import registry
from widgets.runner import MAX_DATA_BYTES, MAX_FRAGMENT_BYTES, check_output

WIDGETS = [w.id for w in registry.all_widgets()]
LOOK = render.Look(name="N", tagline="t", accent="#7fd1a0", link_color=AI_LINK, week_of="October 19, 2026")


@pytest.mark.parametrize("wid", WIDGETS)
def test_metadata(wid):
    w = registry.get(wid)
    assert w.name and w.description and w.topics
    assert w.scope in ("edition", "personal") and w.kind in ("external", "derived", "pipeline")


@pytest.mark.parametrize("wid", WIDGETS)
def test_sample_renders_within_budget(wid):
    w = registry.get(wid)
    data = w.sample_data()
    assert len(json.dumps(data).encode("utf-8")) <= MAX_DATA_BYTES
    assert check_output(w, data, {}) is None
    html = w.render(data, {}, LOOK)
    assert 0 < len(html.encode("utf-8")) <= MAX_FRAGMENT_BYTES
    assert html.lstrip().startswith(("<!--", "<tr")) and html.rstrip().endswith(("-->", "</tr>"))
    assert "<script" not in html.lower() and "{{" not in html


@pytest.mark.parametrize("wid", WIDGETS)
@pytest.mark.parametrize("data", [{}, {"rows": None}, {"rows": [None, 3, "x"]}, {"items": [{}]}, {"papers": [{}]},
                                  {"as_of": "garbage"}])
def test_render_never_raises_on_odd_data(wid, data):
    registry.get(wid).render(data, {}, LOOK)


@pytest.mark.parametrize("wid", [w.id for w in registry.all_widgets() if w.kind == "external" and not w.keys])
def test_keyless_widgets_produce_ok_from_the_recordings_through_every_check(wid):
    """Real-size data through the runner's JSON, size and fragment checks (a 10-row Steam chart once failed)."""
    from widget_fakes import all_fixture_routes, ctx
    from widgets.runner import produce_edition_widgets
    res = produce_edition_widgets(ctx(all_fixture_routes(), week="2026-10-19"), [{"id": wid}])
    assert res.summary() == {wid: "ok"}, res.status


@pytest.mark.parametrize("wid", [w.id for w in registry.all_widgets() if w.kind == "external"])
def test_external_widgets_show_a_source(wid):
    assert "Source:" in registry.get(wid).render(registry.get(wid).sample_data(), {}, LOOK)
