"""Spotlight paper fallback: stubbed summarizer, no network."""
import pytest

import agent2a_summarize_papers as a2a
import agent3_compose
import spotlight_history


def _paper(n):
    return {"id": f"http://arxiv.org/abs/2610.0000{n}v1", "title": f"P{n}", "authors": ["A"], "published": "2026-10-01",
            "pdf_url": f"u{n}", "categories": ["cs.AI"], "scores": {"total": 30 - n}}


def _stub(monkeypatch, failing):
    monkeypatch.setattr(a2a, "download_and_extract", lambda url, pid: "text")

    def fake(paper, text, tpl, client):
        if paper["title"] in failing:
            raise RuntimeError("boom")
        return "A summary."
    monkeypatch.setattr(a2a, "summarize_paper", fake)
    monkeypatch.setattr(a2a, "validate_summary", lambda s, pid: None)


def test_top_pick_summarized_no_fallback(monkeypatch):
    _stub(monkeypatch, set())
    results, sources, replaced = a2a.summarize_with_fallback([_paper(1)], [_paper(2)], "t", None)
    assert [r["title"] for r in results] == ["P1"] and not replaced and len(sources) == 1


def test_next_best_paper_stands_in(monkeypatch):
    _stub(monkeypatch, {"P1", "P2"})
    results, sources, replaced = a2a.summarize_with_fallback([_paper(1)], [_paper(2), _paper(3), _paper(4)], "t", None)
    assert [r["title"] for r in results] == ["P3"] and replaced
    assert sources[0]["ident"] == _paper(3)["id"]


def test_error_when_no_paper_can_be_summarized(monkeypatch):
    _stub(monkeypatch, {"P1", "P2"})
    with pytest.raises(RuntimeError, match="no spotlight paper"):
        a2a.summarize_with_fallback([_paper(1)], [_paper(2)], "t", None)
    with pytest.raises(RuntimeError):
        a2a.summarize_with_fallback([_paper(1)], [], "t", None)


def test_unrecord_only_removes_own_picks(tmp_path):
    d = str(tmp_path)
    spotlight_history.record_spotlight([{"arxiv_id": "2610.00001", "title": "a"}], "run1", False, "", d)
    spotlight_history.record_spotlight([{"arxiv_id": "2610.00002", "title": "b"}], "run0", False, "", d)
    assert spotlight_history.unrecord_spotlight(["2610.00001", "2610.00002"], "run1", False, "", d) == 1
    assert spotlight_history.load_seen_ids(False, "", d) == {"2610.00002"}


def test_agent3_refuses_issue_without_a_summarized_paper():
    ok = {"summary": "text"}
    assert agent3_compose.require_summarized_papers([{"summary": None}, ok]) == [ok]
    with pytest.raises(RuntimeError):
        agent3_compose.require_summarized_papers([{"summary": None}])
