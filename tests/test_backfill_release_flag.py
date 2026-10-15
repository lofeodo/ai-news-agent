"""tools/backfill_release_flag.select_ids: which old runs get flagged as release."""
import importlib.util
import os

spec = importlib.util.spec_from_file_location(
    "backfill_release_flag", os.path.join(os.path.dirname(__file__), "..", "tools", "backfill_release_flag.py"))
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)


def _row(i, started, composed=True, kind=None):
    f = {"started_at": started, "newsletter_composed": composed}
    if kind:
        f["run_kind"] = kind
    return (i, f)


def test_flags_composed_unflagged_monday_morning_runs():
    rows = [
        _row("mon", "2026-06-22T10:00:19.594890"),                  # naive, Monday morning
        _row("mon_aware", "2026-09-21T10:01:45+00:00"),
        _row("sun", "2026-07-05T13:13:38"),                          # Sunday: recovery run
        _row("mon_pm", "2026-09-28T13:23:44+00:00"),                 # Monday afternoon: not by rule
        _row("uncomposed", "2026-09-14T10:00:07+00:00", composed=False),
        _row("flagged", "2026-10-05T10:02:39+00:00", kind="release"),
        _row("debug", "2026-10-12T10:02:39+00:00", kind="debug"),
        ("no_start", {"newsletter_composed": True}),
    ]
    assert bf.select_ids(rows) == ["mon", "mon_aware"]


def test_extra_ids_are_included_only_when_composed_and_unflagged():
    shipped = bf.EXTRA_IDS[0]
    assert bf.select_ids([_row(shipped, "2026-09-28T18:39:12+00:00")]) == [shipped]
    assert bf.select_ids([_row(shipped, "2026-09-28T18:39:12+00:00", kind="debug")]) == []
    assert bf.select_ids([_row(shipped, "2026-09-28T18:39:12+00:00", composed=False)]) == []
    assert bf.select_ids([]) == []
