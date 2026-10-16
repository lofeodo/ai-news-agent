"""Widget storage (agents/widgets/store.py) on the fake Firestore, plus the personal-widget cache, proven with a
test-only personal widget (the real stock watchlist waits for a licensed data source)."""
from datetime import datetime, timedelta, timezone

import pytest

from fakes_firestore import FakeDb
from widgets import registry, store
from widgets.base import ProduceContext, Widget
from widgets.runner import RunResult

NOW = datetime(2026, 10, 18, 16, 0, tzinfo=timezone.utc)


def _result():
    return RunResult(data={"a": {"x": 1}},
                     status={"a": {"status": "ok", "seconds": 0.1},
                             "b": {"status": "failed", "error": "source down"},
                             "c": {"status": "skipped_no_key", "error": "no key (K)"}})


def test_save_and_load_edition():
    db = FakeDb()
    db.collection("pipeline_runs").document("r1").set({"topic": "gaming"})
    summary = store.save_edition(db, "r1", _result(), now=NOW)
    assert summary == {"a": "ok", "b": "failed", "c": "skipped_no_key"}
    run = db.store["pipeline_runs"]["r1"]
    assert run["widgets_summary"] == summary and run["topic"] == "gaming"
    docs = db.store["pipeline_runs/r1/widgets"]
    assert docs["a"] == {"data": {"x": 1}, "produced_at": "2026-10-18T16:00:00Z", "status": "ok"}
    assert docs["b"]["data"] is None and docs["b"]["error"] == "source down"
    assert store.load_edition(db, "r1") == {"a": {"x": 1}}


def test_save_and_load_local(tmp_path):
    store.save_local(str(tmp_path), _result(), now=NOW)
    assert store.load_local(str(tmp_path)) == {"a": {"x": 1}}
    assert (tmp_path / "widgets" / "summary.json").exists()
    assert store.load_local(str(tmp_path / "nothing")) == {}


class _Watchlist(Widget):
    id = "t_watchlist"
    name = "Test watchlist"
    description = "test-only personal widget"
    scope = "personal"
    config_fields = ({"name": "tickers", "type": "tickers", "label": "Tickers", "max": 10, "default": []},)
    calls = 0

    def produce(self, ctx, config):
        type(self).calls += 1
        return {"rows": [{"ticker": t, "price": 1.0} for t in config["tickers"]]}

    def render(self, data, config, look):
        return "<tr><td>" + ",".join(r["ticker"] for r in data["rows"]) + "</td></tr>"

    def sample_data(self):
        return {"rows": []}


@pytest.fixture
def watchlist():
    registry.register(_Watchlist)
    _Watchlist.calls = 0
    yield registry.get("t_watchlist")
    registry.unregister("t_watchlist")


def test_personal_cache_shares_one_fetch(watchlist):
    db = FakeDb()
    ctx = ProduceContext(week="2026-10-19", now=NOW)
    a = store.get_or_produce_personal(db, watchlist, {"tickers": ["aapl", "msft"]}, "2026-10-19", ctx, NOW)
    b = store.get_or_produce_personal(db, watchlist, {"tickers": ["AAPL", "MSFT"]}, "2026-10-19", ctx, NOW)
    assert a == b and _Watchlist.calls == 1
    key = store.cache_key("t_watchlist", {"tickers": ["AAPL", "MSFT"]}, "2026-10-19")
    doc = db.store["widget_cache"][key]
    assert doc["expires_at"] == NOW + timedelta(days=8) and doc["week"] == "2026-10-19"
    # another week or another config is another fetch
    store.get_or_produce_personal(db, watchlist, {"tickers": ["AAPL", "MSFT"]}, "2026-10-26", ctx, NOW)
    store.get_or_produce_personal(db, watchlist, {"tickers": ["SHOP.TO"]}, "2026-10-19", ctx, NOW)
    assert _Watchlist.calls == 3


def test_personal_cache_expired_entry_is_refetched(watchlist):
    db = FakeDb()
    ctx = ProduceContext(week="w", now=NOW)
    store.get_or_produce_personal(db, watchlist, {"tickers": ["A"]}, "w", ctx, NOW)
    later = NOW + timedelta(days=9)
    store.get_or_produce_personal(db, watchlist, {"tickers": ["A"]}, "w", ProduceContext(now=later), later)
    assert _Watchlist.calls == 2


def test_personal_invalid_config_or_failure_returns_none(watchlist, monkeypatch):
    db = FakeDb()
    assert store.get_or_produce_personal(db, watchlist, {"tickers": ["<script>"]}, "w") is None

    def boom(self, ctx, config):
        raise RuntimeError("down")
    monkeypatch.setattr(_Watchlist, "produce", boom)
    assert store.get_or_produce_personal(db, watchlist, {"tickers": ["A"]}, "w", ProduceContext(now=NOW), NOW) is None
    assert "widget_cache" not in db.store
