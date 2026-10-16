"""AGENT1B_LANG_MODE=exceptions: the model lists only non-English articles; anything untrustworthy falls back."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))

import agent1b_fetch_news as a1b  # noqa: E402
from fakes import ScriptedClient, make_articles, response, tool_use  # noqa: E402


def exceptions_reply(checked, exceptions, stop="tool_use"):
    r = response(tool_use("report_language_exceptions", checked=checked, exceptions=exceptions))
    r.stop_reason = stop
    return r


def full_reply(labels):
    return response(tool_use("filter_by_language",
                             articles=[{"index": i, "language": l} for i, l in enumerate(labels)]))


def test_default_mode_is_claude(monkeypatch):
    monkeypatch.delenv("AGENT1B_LANG_MODE", raising=False)
    assert a1b.lang_mode() == "claude"
    monkeypatch.setenv("AGENT1B_LANG_MODE", "garbage")
    assert a1b.lang_mode() == "claude"
    monkeypatch.setenv("AGENT1B_LANG_MODE", " Exceptions ")
    assert a1b.lang_mode() == "exceptions"


def test_default_mode_sends_the_original_full_label_call(monkeypatch):
    monkeypatch.delenv("AGENT1B_LANG_MODE", raising=False)
    c = ScriptedClient(full_reply(["en", "fr", "other"]))
    out = a1b.language_filter_batch(make_articles(3), 1, c)
    assert [a["language"] for a in out] == ["en", "fr"]
    assert c.calls[0]["tools"][0]["name"] == "filter_by_language"


def test_exceptions_mode_keeps_unlisted_as_english_and_drops_other(monkeypatch):
    monkeypatch.setenv("AGENT1B_LANG_MODE", "exceptions")
    c = ScriptedClient(exceptions_reply(4, [{"index": 1, "language": "fr"}, {"index": 3, "language": "other"}]))
    out = a1b.language_filter_batch(make_articles(4), 1, c)
    assert [(a["title"], a["language"]) for a in out] == [("T0", "en"), ("T1", "fr"), ("T2", "en")]
    assert len(c.calls) == 1
    assert c.calls[0]["tools"][0]["name"] == "report_language_exceptions"
    assert c.calls[0]["tool_choice"]["name"] == "report_language_exceptions"


def test_exceptions_mode_all_english_is_a_valid_empty_answer(monkeypatch):
    monkeypatch.setenv("AGENT1B_LANG_MODE", "exceptions")
    c = ScriptedClient(exceptions_reply(3, []))
    assert len(a1b.language_filter_batch(make_articles(3), 1, c)) == 3
    assert len(c.calls) == 1


def test_wrong_checked_count_falls_back_to_full_labelling(monkeypatch):
    monkeypatch.setenv("AGENT1B_LANG_MODE", "exceptions")
    c = ScriptedClient(exceptions_reply(2, []), full_reply(["other", "en", "en"]))
    out = a1b.language_filter_batch(make_articles(3), 1, c)
    assert [a["title"] for a in out] == ["T1", "T2"]
    assert [x["tools"][0]["name"] for x in c.calls] == ["report_language_exceptions", "filter_by_language"]


def test_cut_off_answer_falls_back(monkeypatch):
    monkeypatch.setenv("AGENT1B_LANG_MODE", "exceptions")
    c = ScriptedClient(exceptions_reply(3, [], stop="max_tokens"), full_reply(["en", "en", "en"]))
    assert len(a1b.language_filter_batch(make_articles(3), 1, c)) == 3
    assert len(c.calls) == 2


def test_invalid_entries_are_skipped_not_dropped_as_foreign(monkeypatch):
    monkeypatch.setenv("AGENT1B_LANG_MODE", "exceptions")
    c = ScriptedClient(exceptions_reply(2, [{"index": 9, "language": "other"}, {"index": 0, "language": "en"}, "x"]))
    assert len(a1b.language_filter_batch(make_articles(2), 1, c)) == 2


def test_exceptions_mode_honours_the_topic_language_set(monkeypatch):
    monkeypatch.setenv("AGENT1B_LANG_MODE", "exceptions")
    c = ScriptedClient(exceptions_reply(3, [{"index": 1, "language": "fr"}]))
    out = a1b.language_filter_batch(make_articles(3), 1, c, allowed=("en",))
    assert [a["title"] for a in out] == ["T0", "T2"]      # French dropped for an English-only topic
    c = ScriptedClient(exceptions_reply(2, []))
    assert a1b.language_filter_batch(make_articles(2), 1, c, allowed=("fr",)) == []   # unlisted = English, not allowed


def test_prompt_mentions_exceptions_and_keeps_the_rules():
    p = a1b.LANG_EXCEPTIONS_PROMPT
    assert "report_language_exceptions" in p and "filter_by_language" not in p
    assert "EVERY article" not in p
    assert "function words" in p and "{samples}" in p
