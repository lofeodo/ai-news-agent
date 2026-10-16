"""agent1a's optional abstract-first scoring (AGENT1A_MODE=abstract_first). No network, no keys."""
import pytest

import agent1a_fetch_papers as a1a
import paper_shortlist as ps


def res(i, total=None, upvotes=0):
    return {"id": f"http://arxiv.org/abs/2610.{i:05d}v1", "title": f"P{i}", "abstract": f"abs{i}",
            "pdf_url": f"u{i}", "upvotes": upvotes, "scores": None if total is None else {"total": total}}


# --- mode + K --------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (None, "full"), ("", "full"), ("full", "full"), ("garbage", "full"),
    ("abstract_first", "abstract_first"), (" ABSTRACT_FIRST ", "abstract_first"),
])
def test_mode_parsing(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("AGENT1A_MODE", raising=False)
    else:
        monkeypatch.setenv("AGENT1A_MODE", value)
    assert ps.agent1a_mode() == expected


def test_top_k_env(monkeypatch):
    monkeypatch.delenv("AGENT1A_FULLTEXT_TOP_K", raising=False)
    assert ps.top_k() == ps.DEFAULT_TOP_K
    monkeypatch.setenv("AGENT1A_FULLTEXT_TOP_K", "5")
    assert ps.top_k() == 5
    monkeypatch.setenv("AGENT1A_FULLTEXT_TOP_K", "0")
    assert ps.top_k() == 1
    monkeypatch.setenv("AGENT1A_FULLTEXT_TOP_K", "x")
    assert ps.top_k() == ps.DEFAULT_TOP_K


# --- picking ---------------------------------------------------------------

def test_pick_takes_top_k_by_abstract_total():
    stage1 = [res(0, 10), res(1, 30), res(2, 20), res(3, 25)]
    assert ps.pick_for_second_pass(stage1, 2) == [1, 3]


def test_pick_breaks_ties_by_upvotes_then_order():
    stage1 = [res(0, 20, upvotes=1), res(1, 20, upvotes=9), res(2, 20, upvotes=9)]
    assert ps.pick_for_second_pass(stage1, 2) == [1, 2]


def test_failed_abstract_pass_always_goes_to_second_pass():
    stage1 = [res(0, 30), res(1, None), res(2, 20)]
    assert ps.pick_for_second_pass(stage1, 1) == [0, 1]


def test_k_larger_than_pool_picks_everything():
    stage1 = [res(0, 1), res(1, 2)]
    assert ps.pick_for_second_pass(stage1, 8) == [0, 1]


# --- merging ---------------------------------------------------------------

def test_second_pass_papers_outrank_abstract_only_even_with_lower_totals():
    stage1 = [res(0, 40), res(1, 30), res(2, 20)]
    picked = [1, 2]
    stage2 = [res(1, 25), res(2, 22)]
    merged = ps.merge_stages(stage1, picked, stage2)
    assert [m["title"] for m in merged] == ["P1", "P2", "P0"]
    assert [m["scoring_stage"] for m in merged] == [2, 2, 1]


def test_failed_second_pass_keeps_abstract_score_in_the_lower_group():
    stage1 = [res(0, 40), res(1, 30)]
    stage2 = [res(0, None)]
    merged = ps.merge_stages(stage1, [0], stage2)
    assert [m["title"] for m in merged] == ["P0", "P1"]
    assert [m["scoring_stage"] for m in merged] == [1, 1]
    assert merged[0]["scores"] == {"total": 40}


def test_total_failures_sort_last():
    merged = ps.merge_stages([res(0, None), res(1, 5)], [0], [res(0, None)])
    assert [m["title"] for m in merged] == ["P1", "P0"]


def test_summary_counts():
    stage1 = [res(0, 40), res(1, 30), res(2, 20)]
    merged = ps.merge_stages(stage1, [0, 1], [res(0, 38), res(1, None)])
    s = ps.summary(3, [0, 1], merged, 2)
    assert s == {"mode": "abstract_first", "papers": 3, "top_k": 2, "second_pass": 2,
                 "second_pass_scored": 1, "abstract_only": 2}


# --- agent1a wiring --------------------------------------------------------

def test_abstract_pass_never_downloads_a_pdf(monkeypatch):
    seen = {}
    monkeypatch.setattr(a1a, "download_and_extract", lambda *a: pytest.fail("PDF downloaded in stage 1"))
    monkeypatch.setattr(a1a, "score_paper", lambda paper, text: seen.update(text=text) or {"total": 7})
    out = a1a.process_paper_abstract(res(0))
    assert out["scores"] == {"total": 7} and seen["text"] == "abs0" and out["error"] is None


def test_abstract_pass_error_is_recorded_not_raised(monkeypatch):
    def boom(paper, text):
        raise RuntimeError("api down")
    monkeypatch.setattr(a1a, "score_paper", boom)
    out = a1a.process_paper_abstract(res(0))
    assert out["scores"] is None and "api down" in out["error"]


def test_score_abstract_first_downloads_only_the_top_k(monkeypatch):
    totals = {f"abs{i}": t for i, t in enumerate([5, 40, 12, 33, 8, 21])}
    downloads = []
    monkeypatch.setattr(a1a, "score_paper",
                        lambda paper, text: {"total": totals[paper["abstract"]] + (100 if text != paper["abstract"] else 0)})
    monkeypatch.setattr(a1a, "download_and_extract", lambda url, pid: downloads.append(url) or "FULL TEXT")
    papers = [res(i) for i in range(6)]
    merged, summary = a1a.score_abstract_first(papers, 2)
    assert sorted(downloads) == ["u1", "u3"]                 # abstract totals 40 and 33
    assert [m["title"] for m in merged][:2] == ["P1", "P3"]  # full-text papers lead
    assert summary["second_pass"] == 2 and summary["abstract_only"] == 4
    assert merged[0]["scoring_stage"] == 2 and merged[-1]["scoring_stage"] == 1
