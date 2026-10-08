import run_kind


def test_missing_field_counts_as_release():
    assert run_kind.is_release({})
    assert run_kind.is_release(None)
    assert run_kind.is_release({"run_kind": "release"})


def test_debug_is_not_release():
    assert not run_kind.is_release({"run_kind": "debug"})


def test_pick_release_skips_newer_debug_runs():
    rows = [("c", {"run_kind": "debug"}), ("b", {"run_kind": "debug"}), ("a", {}), ("z", {"run_kind": "release"})]
    assert run_kind.pick_release_id(rows) == "a"


def test_pick_release_none_when_all_debug():
    assert run_kind.pick_release_id([("a", {"run_kind": "debug"})]) is None
    assert run_kind.pick_release_id([]) is None
