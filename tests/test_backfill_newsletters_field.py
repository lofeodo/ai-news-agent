"""tools/backfill_newsletters_field.py: legacy subscriber docs get the C1 fields; nothing else changes."""
import importlib.util
import os
from datetime import datetime, timezone

from fakes_firestore import FakeDb

spec = importlib.util.spec_from_file_location(
    "backfill_newsletters_field", os.path.join(os.path.dirname(__file__), "..", "tools", "backfill_newsletters_field.py"))
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)

T = datetime(2026, 1, 5, tzinfo=timezone.utc)


def _db():
    return FakeDb({"subscribers": {
        "on@example.com":      {"email": "on@example.com", "active": True, "token": "t1", "confirmed_at": T},
        "off@example.com":     {"email": "off@example.com", "active": False, "token": "t2"},
        "done@example.com":    {"email": "done@example.com", "active": True, "newsletters": ["ai"],
                                "subscribed_any": True, "newsletter_meta": {"ai": {"consent_text_version": "x"}}},
        "fin@example.com":     {"email": "fin@example.com", "active": False, "newsletters": ["finance"],
                                "subscribed_any": True},
    }})


def test_dry_run_counts_and_changes_nothing(capsys):
    db = _db()
    before = {k: dict(v) for k, v in db.store["subscribers"].items()}
    counts = bf.run(db, apply=False)
    assert counts == {"docs": 4, "to_update": 2, "ai_subscribers": 2, "subscribed_to_any": 3}
    assert db.store["subscribers"] == before


def test_apply_writes_the_c1_fields():
    db = _db()
    bf.run(db, apply=True)
    s = db.store["subscribers"]
    assert s["on@example.com"]["newsletters"] == ["ai"] and s["on@example.com"]["subscribed_any"] is True
    assert s["on@example.com"]["newsletter_meta"]["ai"]["subscribed_at"] == T
    assert s["on@example.com"]["active"] is True and s["on@example.com"]["token"] == "t1"
    assert s["off@example.com"]["newsletters"] == [] and s["off@example.com"]["subscribed_any"] is False
    assert "newsletter_meta" not in s["off@example.com"]
    assert bf.run(db, apply=False)["to_update"] == 0   # a second run has nothing left to do


def test_list_and_flag_disagreeing_follows_the_flag():
    assert bf.plan_update({"active": False, "newsletters": ["ai", "gaming"], "subscribed_any": True}) == {
        "newsletters": ["gaming"]}


def test_output_never_names_an_address(capsys, monkeypatch):
    import sys
    from types import SimpleNamespace
    db = _db()
    fake_fs = SimpleNamespace(Client=lambda: db)
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", fake_fs)
    import google.cloud
    monkeypatch.setattr(google.cloud, "firestore", fake_fs, raising=False)
    monkeypatch.setattr(sys, "argv", ["backfill"])
    assert bf.main() == 0
    out = capsys.readouterr().out
    assert "@" not in out and "2 doc(s) would be updated" in out
