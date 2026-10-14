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


def test_pick_sent_skips_a_draft_that_has_not_been_mailed_yet():
    rows = [("draft", {}), ("last_week", {"agent4_send_summary": {"sent": 15}})]
    assert run_kind.pick_release_id(rows) == "draft"
    assert run_kind.pick_release_id(rows, sent_only=True) == "last_week"


def test_pick_sent_skips_a_mailed_debug_run_and_returns_none_when_nothing_was_mailed():
    assert run_kind.pick_release_id([("d", {"run_kind": "debug", "agent4_send_summary": {"sent": 1}})], sent_only=True) is None
    assert run_kind.pick_release_id([("draft", {})], sent_only=True) is None
