"""The full-section HN rank is carried into agent2b's shortlist pool, so agent3's label matches the full run."""
import json
from types import SimpleNamespace

import agent2b_summarize_news as a2b
import agent3_compose as a3
import news_shortlist as ns
from test_news_shortlist import SummaryClient, _out, art


def test_hn_context_ranks_within_the_whole_section_with_ties():
    by_cat = {"A": [art(0, hn=50), art(1, hn=10), art(2), art(3, hn=50)], "B": [art(4, hn=7)]}
    ctx = ns.hn_context(by_cat)
    assert ctx[0] == {"hn_rank": 1, "hn_of": 3}
    assert ctx[3] == {"hn_rank": 1, "hn_of": 3}      # a tie shares the rank
    assert ctx[1] == {"hn_rank": 3, "hn_of": 3}
    assert 2 not in ctx                               # no HN score, no rank
    assert ctx[4] == {"hn_rank": 1, "hn_of": 1}       # ranked inside its own section only


def test_agent3_label_uses_the_carried_rank_when_present():
    carried = {"hn_score": 120, "hn_rank": 9, "hn_of": 41}
    assert a3._hn_label(carried, [120, 500]) == "hn: 120 points (#9 of 41 HN stories in this section)"


def test_agent3_label_unchanged_without_a_carried_rank():
    assert a3._hn_label({"hn_score": 120}, [120, 500]) == "hn: 120 points (#2 of 2 HN stories in this section)"
    assert a3._hn_label({}, [120]) == "hn: unknown (not posted to HN)"


def test_run_shortlist_attaches_the_full_section_rank(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT2B_MODE", "shortlist")
    by_cat = {"Safety & Alignment": [art(i, desc="", hn=100 - i) for i in range(8)]}
    (tmp_path / "news_filtered.json").write_text(json.dumps({"by_category": by_cat}), encoding="utf-8")
    monkeypatch.setattr(a2b, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(a2b, "USE_FIRESTORE", False)
    monkeypatch.setattr(a2b.tracing, "make_client", lambda *a, **k: SummaryClient())
    monkeypatch.setattr(a2b.tracing, "flush", lambda: None)
    monkeypatch.setattr(a2b, "fetch_article_text", lambda url: f"page text of {url} " * 20)

    class PickLowest:     # shortlists the 3 worst-ranked articles, so the carried rank is far from 1
        def __call__(self, category, candidates, prompt, client):
            low = sorted(candidates, key=lambda c: -c["_idx"])[:3]
            return SimpleNamespace(picks=low, runners_up=[])

    monkeypatch.setattr(ns, "_default_select", lambda: PickLowest())
    a2b.run("t")
    arts = _out(tmp_path)["by_category"]["Safety & Alignment"]
    assert len(arts) == 3
    for a in arts:
        # title "T5" has 95 points: #6 of 8 in the whole section, not #3 of 3 in the shortlist
        assert a["hn_of"] == 8 and a["hn_rank"] == 1 + int(a["title"][1:])
