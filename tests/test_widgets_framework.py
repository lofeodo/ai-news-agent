"""Widget framework (agents/widgets/): registry, config validation, and the bounded runner."""
import time

import pytest

from widgets import registry
from widgets.base import ProduceContext, Widget, WidgetSkip
from widgets.config import ConfigError, config_hash, validate
from widgets.runner import produce_edition_widgets


def _make(wid, produce=None, render=None, **attrs):
    def _produce(self, ctx, config):
        return produce(ctx, config) if produce else {"n": 1}

    def _render(self, data, config, look):
        return render(data) if render else f"<tr><td>{data}</td></tr>"

    cls = type(f"W_{wid}", (Widget,), {"id": wid, "name": wid, "description": "test",
                                        "produce": _produce, "render": _render,
                                        "sample_data": lambda self: {"n": 0}, **attrs})
    registry.register(cls)
    return registry.get(wid)


@pytest.fixture
def make_widget():
    made = []

    def make(wid, *a, **kw):
        made.append(wid)
        return _make(wid, *a, **kw)

    yield make
    for wid in made:
        registry.unregister(wid)


def ctx():
    return ProduceContext(topic="demo", run_id="r1", week="2026-10-19")


def test_ok_widget_produces_data(make_widget):
    make_widget("t_ok")
    res = produce_edition_widgets(ctx(), [{"id": "t_ok"}])
    assert res.data == {"t_ok": {"n": 1}}
    assert res.summary() == {"t_ok": "ok"}


def test_exception_is_failed_not_raised(make_widget):
    def boom(ctx, config):
        raise RuntimeError("source down")
    make_widget("t_boom", produce=boom)
    res = produce_edition_widgets(ctx(), [{"id": "t_boom"}])
    assert res.data == {}
    assert res.status["t_boom"]["status"] == "failed"
    assert "source down" in res.status["t_boom"]["error"]


def test_timeout(make_widget):
    make_widget("t_slow", produce=lambda c, cfg: time.sleep(1.0) or {"n": 1})
    make_widget("t_fast")
    t0 = time.monotonic()
    res = produce_edition_widgets(ctx(), [{"id": "t_slow"}, {"id": "t_fast"}], timeout=0.2, budget=5)
    assert time.monotonic() - t0 < 0.9
    assert res.summary() == {"t_slow": "timed_out", "t_fast": "ok"}


def test_a_widget_may_ask_for_a_longer_timeout(make_widget):
    make_widget("t_patient", produce=lambda c, cfg: time.sleep(0.4) or {"n": 1}, timeout=2)
    make_widget("t_hasty", produce=lambda c, cfg: time.sleep(0.4) or {"n": 1})
    res = produce_edition_widgets(ctx(), [{"id": "t_patient"}, {"id": "t_hasty"}], timeout=0.2, budget=5)
    assert res.summary() == {"t_patient": "ok", "t_hasty": "timed_out"}


def test_budget_exhaustion(make_widget):
    make_widget("t_b1", produce=lambda c, cfg: time.sleep(1.0) or {"n": 1})
    make_widget("t_b2", produce=lambda c, cfg: time.sleep(1.0) or {"n": 1})
    res = produce_edition_widgets(ctx(), [{"id": "t_b1"}, {"id": "t_b2"}], timeout=5, budget=0.2, max_workers=1)
    assert res.summary() == {"t_b1": "timed_out", "t_b2": "timed_out"}
    assert "budget" in res.status["t_b2"]["error"]


def test_oversized_data_and_fragment(make_widget):
    make_widget("t_bigdata", produce=lambda c, cfg: {"x": "a" * 25_000})
    make_widget("t_bightml", render=lambda d: "<tr><td>" + "b" * 9_000 + "</td></tr>")
    res = produce_edition_widgets(ctx(), [{"id": "t_bigdata"}, {"id": "t_bightml"}])
    assert res.summary() == {"t_bigdata": "failed", "t_bightml": "failed"}
    assert "max 20,000" in res.status["t_bigdata"]["error"]
    assert "fragment" in res.status["t_bightml"]["error"]


def test_not_json_and_render_raising(make_widget):
    make_widget("t_set", produce=lambda c, cfg: {"x": {1, 2}})
    make_widget("t_badrender", render=lambda d: 1 / 0)
    res = produce_edition_widgets(ctx(), [{"id": "t_set"}, {"id": "t_badrender"}])
    assert res.summary() == {"t_set": "failed", "t_badrender": "failed"}


def test_missing_key_is_skipped(make_widget, monkeypatch):
    monkeypatch.delenv("T_WIDGET_KEY", raising=False)
    make_widget("t_keyed", keys=("T_WIDGET_KEY",))
    res = produce_edition_widgets(ctx(), [{"id": "t_keyed"}])
    assert res.summary() == {"t_keyed": "skipped_no_key"}
    monkeypatch.setenv("T_WIDGET_KEY", "x")
    assert produce_edition_widgets(ctx(), [{"id": "t_keyed"}]).summary() == {"t_keyed": "ok"}


def test_unknown_widget_skip_and_stale(make_widget):
    def skip(c, cfg):
        raise WidgetSkip("nothing this week")
    make_widget("t_skip", produce=skip)
    make_widget("t_stale", produce=lambda c, cfg: {"as_of": "2020-01-01"}, max_age_hours=24)
    res = produce_edition_widgets(ctx(), [{"id": "nope"}, {"id": "t_skip"}, {"id": "t_stale"}])
    assert res.summary() == {"nope": "failed", "t_skip": "empty", "t_stale": "stale"}


def test_bad_config_fails_before_produce(make_widget):
    called = []
    make_widget("t_cfg", produce=lambda c, cfg: called.append(1) or {"n": 1},
                config_fields=({"name": "count", "type": "int", "min": 1, "max": 5, "default": 3},))
    res = produce_edition_widgets(ctx(), [{"id": "t_cfg", "config": {"count": 9}}])
    assert res.summary() == {"t_cfg": "failed"} and not called
    res = produce_edition_widgets(ctx(), [{"id": "t_cfg"}])
    assert res.configs == {"t_cfg": {"count": 3}}


def test_non_commercial_widget_absent_from_custom_catalog(make_widget):
    make_widget("t_nc", commercial_use=False)
    make_widget("t_c")
    custom = {w["id"] for w in registry.catalog(for_custom=True)}
    assert "t_c" in custom and "t_nc" not in custom
    assert "t_nc" in {w["id"] for w in registry.catalog(for_custom=False)}


def test_catalog_hides_keyed_widgets_without_key_and_filters_topic(make_widget, monkeypatch):
    monkeypatch.delenv("T_WIDGET_KEY", raising=False)
    make_widget("t_k", keys=("T_WIDGET_KEY",))
    make_widget("t_fin", topics=("finance",))
    ids = {w["id"] for w in registry.catalog(topic="gaming")}
    assert "t_k" not in ids and "t_fin" not in ids
    assert "t_fin" in {w["id"] for w in registry.catalog(topic="finance")}
    assert registry.get("t_fin") in registry.for_topic("finance")


def test_personal_widgets_are_premium(make_widget):
    w = make_widget("t_personal", scope="personal")
    assert w.premium and w.info()["premium"]
    res = produce_edition_widgets(ctx(), [{"id": "t_personal"}])
    assert res.summary() == {"t_personal": "failed"}


def test_register_rejects_duplicates(make_widget):
    make_widget("t_dup")
    with pytest.raises(ValueError):
        _make("t_dup")


# config validation

TICKERS = ({"name": "tickers", "type": "tickers", "label": "Tickers", "max": 10, "default": []},)


def test_tickers_validation():
    assert validate(TICKERS, {"tickers": ["aapl", " shop.to ", "BRK-B"]}) == {"tickers": ["AAPL", "SHOP.TO", "BRK-B"]}
    for bad in (["<b>"], ["TOOLONGTICKER1"], ["A"] * 2, [f"T{i}" for i in range(11)], "AAPL", [1]):
        with pytest.raises(ConfigError):
            validate(TICKERS, {"tickers": bad})


def test_choice_int_bool_and_unknown():
    fields = ({"name": "c", "type": "choice", "options": ["a", "b"], "default": "a"},
              {"name": "n", "type": "int", "min": 1, "max": 5, "default": 2},
              {"name": "f", "type": "bool", "default": False})
    assert validate(fields, None) == {"c": "a", "n": 2, "f": False}
    assert validate(fields, {"c": "b", "n": 5, "f": True}) == {"c": "b", "n": 5, "f": True}
    with pytest.raises(ConfigError) as e:
        validate(fields, {"c": "z", "n": True, "f": "yes", "extra": 1})
    assert len(e.value.errors) == 4


def test_config_hash_is_stable_and_order_free():
    assert config_hash({"a": 1, "b": [1, 2]}) == config_hash({"b": [1, 2], "a": 1})
    assert config_hash({"a": 1}) != config_hash({"a": 2})
    assert len(config_hash({})) == 16
