"""release_tracker (3D printing) on the recorded GitHub responses."""
import requests

from widget_fakes import FakeResponse, ctx, fixture_json
from widgets import registry
from widgets.release_tracker import clean_version
from widgets.runner import produce_edition_widgets

W = registry.get("release_tracker")


def _routes(overrides=None):
    rec = fixture_json("github_releases_latest.json")
    routes = dict(overrides or {})   # first match wins, so overrides go first
    for repo, r in rec.items():
        routes.setdefault(f"/repos/{repo}/releases/latest", FakeResponse(r["status"], r["body"]))
    return routes


def test_fixture_render():
    data = W.produce(ctx(_routes()), {})
    names = [r["name"] for r in data["rows"]]
    assert names[0] == "Marlin firmware"        # newest first (2026-10-12)
    assert "Prusa firmware (Buddy)" in names and len(names) == 7
    marlin = data["rows"][0]
    assert marlin["version"] == "2.1.2.9" and marlin["new"] is True
    assert {r["name"]: r["new"] for r in data["rows"]}["PrusaSlicer"] is False
    html = W.render(data, {}, None)
    assert "NEW" in html and "1 new this week" in html and "Source:" in html
    assert 'href="https://github.com/MarlinFirmware/Marlin/releases/tag/2.1.2.9"' in html


def test_repo_with_no_releases_is_skipped_quietly():
    routes = _routes({"/repos/prusa3d/PrusaSlicer/releases/latest": FakeResponse(404, {"message": "Not Found"})})
    names = [r["name"] for r in W.produce(ctx(routes), {})["rows"]]
    assert "PrusaSlicer" not in names and len(names) == 6


def test_api_errors_drop_rows_and_all_failing_fails_the_widget():
    routes = _routes({"/repos/SoftFever/OrcaSlicer": requests.ConnectionError("boom"),
                      "/repos/bambulab/BambuStudio": FakeResponse(403, {"message": "rate limit"})})
    assert len(W.produce(ctx(routes), {})["rows"]) == 5
    res = produce_edition_widgets(ctx({"api.github.com": FakeResponse(500, {})}), [{"id": "release_tracker"}])
    assert res.summary() == {"release_tracker": "failed"}
    res = produce_edition_widgets(ctx({"api.github.com": FakeResponse(404, {})}), [{"id": "release_tracker"}])
    assert res.summary() == {"release_tracker": "empty"}


def test_prereleases_and_odd_payloads_are_ignored():
    routes = _routes({"/repos/prusa3d/PrusaSlicer": FakeResponse(200, {"tag_name": "v3.0.0-alpha", "prerelease": True,
                                                                         "published_at": "2026-10-17T00:00:00Z"}),
                      "/repos/Ultimaker/Cura": FakeResponse(200, ["not", "a", "dict"]),
                      "/repos/SoftFever/OrcaSlicer": FakeResponse(200, {"tag_name": "v1", "published_at": "soon"})})
    names = [r["name"] for r in W.produce(ctx(routes), {})["rows"]]
    assert not {"PrusaSlicer", "UltiMaker Cura", "OrcaSlicer"} & set(names)


def test_clean_version():
    assert [clean_version(t) for t in ("version_2.9.6", "v2.4.2", "5.13.0", "V7.3.0", "vNext")] == \
        ["2.9.6", "2.4.2", "5.13.0", "7.3.0", "vNext"]


def test_render_survives_garbage():
    assert W.render({"rows": [None, {"name": "<b>", "url": "javascript:x"}]}, {}, None).count("<b>") == 0
    assert W.render({}, {}, None) == ""
