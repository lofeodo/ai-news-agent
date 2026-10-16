"""Pure helpers of the language-filter eval (no network)."""
from evals.run_lang_filter_eval import compare, disagreements, labels_from_kept


def test_labels_from_kept_marks_missing_as_dropped():
    batch = [{"url": "a"}, {"url": "b"}, {"url": "c"}]
    kept = [{"url": "a", "language": "en"}, {"url": "c", "language": "fr"}]
    assert labels_from_kept(batch, kept) == {"a": "en", "b": "dropped", "c": "fr"}


def test_compare_separates_the_two_owner_rules():
    ref = {"a": "en", "b": "dropped", "c": "fr", "d": "en"}
    got = {"a": "en", "b": "en", "c": "dropped", "d": "fr"}
    c = compare(ref, got)
    assert c["foreign_through"] == ["b"]
    assert c["wrongly_dropped"] == ["c"]
    assert c["relabelled"] == ["d"]
    assert c["n"] == 4


def test_disagreements_lists_only_split_urls():
    arms = {"x": {"a": "en", "b": "dropped"}, "y": {"a": "en", "b": "en"}}
    assert disagreements(arms) == [{"url": "b", "x": "dropped", "y": "en"}]
