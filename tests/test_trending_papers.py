"""Trending-paper selection: HF upvote ranking, spotlight dedup, traction points, scoring totals, links."""
from datetime import datetime, timezone
from types import SimpleNamespace

import agent1a_fetch_papers as a1a
import click_links
import spotlight_history as sh
import trending_papers as tp


def _c(i, up):
    return {"arxiv_id": f"2610.{i:05d}", "title": f"P{i}", "upvotes": up}


def test_base_id_and_hf_url():
    assert tp.base_arxiv_id("http://arxiv.org/abs/2610.07557v2") == "2610.07557"
    assert tp.hf_paper_url("http://arxiv.org/abs/2610.07557v2") == "https://huggingface.co/papers/2610.07557"


def test_rank_takes_most_upvoted_and_refills_past_seen():
    cands = [_c(i, i) for i in range(1, 11)]          # upvotes 1..10
    top3 = tp.rank_candidates(cands, set(), 3)
    assert [c["upvotes"] for c in top3] == [10, 9, 8]
    # the 10- and 9-upvote papers were already spotlighted: pool refills from the next ranks
    seen = {cands[9]["arxiv_id"], cands[8]["arxiv_id"]}
    refilled = tp.rank_candidates(cands, seen, 3)
    assert [c["upvotes"] for c in refilled] == [8, 7, 6]


def test_traction_points_span_one_to_max_and_ties_share_rank():
    ups = [100, 50, 10, 10, 1]
    pts = [tp.traction_points(u, ups, 8) for u in ups]
    assert pts[0] == 8 and pts[-1] == 1
    assert pts[2] == pts[3]
    assert tp.traction_points(5, [5], 8) == 8


def test_attach_traction_adds_fields():
    papers = [{"id": "http://arxiv.org/abs/2610.00001v1"}, {"id": "http://arxiv.org/abs/2610.00002v1"}]
    out = tp.attach_traction(papers, {"2610.00001": 90, "2610.00002": 3}, 8)
    assert out[0]["upvotes"] == 90 and out[0]["community_traction"] == 8
    assert out[1]["community_traction"] == 1


def test_fetch_hf_candidates_dedupes_across_days_and_skips_bad_ids():
    def item(i, up):
        return {"paper": {"id": i, "upvotes": up, "title": "t"}}

    class Resp:
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data

    class Sess:
        def get(self, url, params=None, **kw):
            return Resp([item("2610.00001", 5), item("not-an-id", 99)])

    out = tp.fetch_hf_candidates("u", 2, now=datetime(2026, 10, 7, tzinfo=timezone.utc), session=Sess())
    assert [c["arxiv_id"] for c in out] == ["2610.00001"]


def test_keep_ai_papers():
    kept = tp.keep_ai_papers([{"categories": ["cs.CV"]}, {"categories": ["cs.CV", "cs.LG"]}])
    assert len(kept) == 1


def test_spotlight_history_local_roundtrip_and_retry_idempotence(tmp_path):
    d = str(tmp_path)
    assert sh.load_seen_ids(False, "p", d) == set()
    assert sh.record_spotlight([{"arxiv_id": "2610.00001", "title": "A"}], "run1", False, "p", d) == 1
    assert sh.load_seen_ids(False, "p", d, run_id="run2") == {"2610.00001"}
    # a retry of the same run does not see its own earlier pick as already spotlighted
    assert sh.load_seen_ids(False, "p", d, run_id="run1") == set()
    sh.record_spotlight([{"arxiv_id": "2610.00001", "title": "A"}], "run1", False, "p", d)
    assert len(sh.load_seen_ids(False, "p", d, run_id="other")) == 1


def test_spotlight_history_read_failure_degrades_to_no_history(tmp_path):
    (tmp_path / "spotlighted_papers.json").write_text("{not json", encoding="utf-8")
    assert sh.load_seen_ids(False, "p", str(tmp_path)) == set()


def test_finalize_scores_recomputes_total_with_traction():
    dims = dict(novelty=4, rigor=2, reproducibility=2, clarity=3, practical_applicability=3,
                significance=3, disruption_potential=4, wow_factor=5)
    out = a1a.finalize_scores({**dims, "total": 99, "reasoning": "r"}, {"upvotes": 40, "community_traction": 6})
    assert out["claude_total"] == 26 and out["total"] == 32 and out["community_traction"] == 6


def test_score_paper_prompt_carries_upvotes(monkeypatch):
    seen = []

    class Stub:
        def __init__(self, *a, **k): self.messages = self
        def create(self, **kw):
            seen.append(kw)
            return SimpleNamespace(content=[SimpleNamespace(input={"total": 1})])

    monkeypatch.setattr(a1a.anthropic, "Anthropic", Stub)
    a1a.score_paper({"title": "t", "abstract": "a", "upvotes": 123}, "text")
    assert "123 upvotes" in seen[0]["messages"][0]["content"]


def test_click_links_prefers_hf_url():
    doc = {"paper_summaries": [{"title": "A", "hf_url": "https://huggingface.co/papers/2610.00001",
                                "pdf_url": "https://arxiv.org/pdf/2610.00001"}]}
    urls = [v["url"] for v in click_links.build_link_map(doc).values()]
    assert urls == ["https://huggingface.co/papers/2610.00001"]


def test_summary_heading_is_stripped_before_storing():
    import agent2a_summarize_papers as a2a
    from test_injection_prompts import TextClient
    client = TextClient("# Hook\n\nA robot learned to cook. It now makes omelettes.")
    out = a2a.summarize_paper({"title": "t"}, "text", "{title} {text}", client)
    assert out == "A robot learned to cook. It now makes omelettes."
    assert a2a.summarize_paper({"title": "t"}, "text", "{title} {text}", TextClient("Plain hook.")) == "Plain hook."
