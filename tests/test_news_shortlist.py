"""agent2b's optional shortlist mode (AGENT2B_MODE=shortlist). No network, no keys."""
import json
from types import SimpleNamespace

import pytest

import agent2b_summarize_news as a2b
import news_shortlist as ns


class Sel:
    """Fake of agent3_compose.select_with_runners_up: picks the first `picks` candidates, then the
    next `runners` as runners-up. Records every call."""

    def __init__(self, picks=1, runners=1, fail_for=()):
        self.picks, self.runners, self.fail_for, self.calls = picks, runners, set(fail_for), []

    def __call__(self, category, candidates, prompt, client):
        self.calls.append((category, [c["_idx"] for c in candidates]))
        if category in self.fail_for:
            raise RuntimeError("boom")
        usable = [c for c in candidates if (c.get("description") or "").strip()]
        return SimpleNamespace(picks=usable[:self.picks],
                               runners_up=usable[self.picks:self.picks + self.runners])


def art(i, lang="en", desc="", hn=None):
    return {"title": f"T{i}", "url": f"https://e.com/{i}", "language": lang,
            "description": desc, **({"hn_score": hn} if hn is not None else {})}


# --- mode switch -----------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (None, "all"), ("", "all"), ("all", "all"), ("ALL", "all"), ("nonsense", "all"),
    ("shortlist", "shortlist"), (" Shortlist ", "shortlist"),
])
def test_mode_parsing(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("AGENT2B_MODE", raising=False)
    else:
        monkeypatch.setenv("AGENT2B_MODE", value)
    assert ns.agent2b_mode() == expected


# --- lead text -------------------------------------------------------------

def test_lead_text_prefers_description_then_page_text():
    assert ns.lead_text({"description": "  desc   here "}, "page text") == "desc here"
    assert ns.lead_text({"description": ""}, "page   text\nlead") == "page text lead"
    assert ns.lead_text({}, None) == ""
    assert len(ns.lead_text({}, "x" * 1000)) == ns.SNIPPET_CHARS


def test_make_candidates_strips_summary_and_keeps_position():
    cands = ns.make_candidates([art(0), art(1, desc="d1")], ["page zero", None])
    assert [c["_idx"] for c in cands] == [0, 1]
    assert cands[0]["description"] == "page zero" and cands[1]["description"] == "d1"
    assert all(c["summary"] is None for c in cands)


# --- per category ----------------------------------------------------------

def test_category_keeps_picks_and_runners_up():
    arts = [art(i) for i in range(6)]
    texts = [f"text {i}" for i in range(6)]
    kept = ns.shortlist_category("C", arts, texts, "p", None, select_fn=Sel(picks=2, runners=2))
    assert len(kept) == 4 and kept <= set(range(6))


def test_category_runs_english_pass_only_when_french_present():
    sel = Sel(picks=1, runners=0)
    ns.shortlist_category("C", [art(0), art(1)], ["a", "b"], "p", None, select_fn=sel)
    assert len(sel.calls) == 1

    sel = Sel(picks=1, runners=0)
    arts = [art(0, lang="fr"), art(1, lang="fr"), art(2), art(3)]
    kept = ns.shortlist_category("C", arts, ["a", "b", "c", "d"], "p", None, select_fn=sel)
    assert len(sel.calls) == 2
    assert sel.calls[1][1] == [2, 3]                   # English-only pool
    assert kept & {2, 3}                               # the EN variant has a candidate


def test_category_with_only_french_skips_second_pass():
    sel = Sel(picks=1, runners=0)
    ns.shortlist_category("C", [art(0, lang="fr")], ["a"], "p", None, select_fn=sel)
    assert len(sel.calls) == 1


# --- across categories -----------------------------------------------------

def test_global_indices_follow_flattened_order():
    by_cat = {"A": [art(0), art(1)], "B": [art(2), art(3), art(4)]}
    texts = {i: f"t{i}" for i in range(5)}
    kept, summary = ns.shortlist_all(by_cat, texts, "p", None, select_fn=Sel(picks=1, runners=0))
    assert kept == {0, 2}
    assert summary["articles_in"] == 5 and summary["shortlisted"] == 2
    assert summary["by_category"]["B"] == {"in": 3, "kept": 1, "failed": False}


def test_failed_category_is_kept_whole_and_flagged():
    by_cat = {"A": [art(0), art(1)], "B": [art(2), art(3)]}
    texts = {i: "t" for i in range(4)}
    kept, summary = ns.shortlist_all(by_cat, texts, "p", None, select_fn=Sel(picks=1, runners=0, fail_for={"B"}))
    assert {2, 3} <= kept and 0 in kept
    assert summary["by_category"]["B"]["failed"] is True
    assert summary["by_category"]["A"]["failed"] is False


def test_empty_category_is_fine():
    kept, summary = ns.shortlist_all({"A": [], "B": [art(0)]}, {0: "t"}, "p", None,
                                     select_fn=Sel(picks=1, runners=0))
    assert kept == {0} and summary["by_category"]["A"] == {"in": 0, "kept": 0, "failed": False}


# --- assemble_results ------------------------------------------------------

def test_assemble_default_still_marks_unprocessed():
    by_cat = {"A": [art(0), art(1)]}
    out, flat, _ = a2b.assemble_results(by_cat, {0: {**art(0), "summary": "s"}})
    assert out["A"][0]["summary"] == "s"
    assert out["A"][1]["summary"] is None and out["A"][1]["summary_error"] == "not processed"
    assert len(flat) == 2


def test_assemble_only_indices_omits_the_rest():
    by_cat = {"A": [art(0), art(1)], "B": [art(2)]}
    results = {1: {**art(1), "summary": "s1"}, 2: {**art(2), "summary": "s2"}}
    out, flat, sources = a2b.assemble_results(by_cat, results, only_indices={1, 2})
    assert [a["title"] for a in out["A"]] == ["T1"] and [a["title"] for a in out["B"]] == ["T2"]
    assert len(flat) == 2 and not any(a.get("summary_error") == "not processed" for a in flat)


# --- run() end to end, local mode -----------------------------------------

class SummaryClient:
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        return SimpleNamespace(content=[SimpleNamespace(text="A short summary.")],
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5), stop_reason="end_turn")


def _setup(monkeypatch, tmp_path, n=8):
    by_cat = {"Safety & Alignment": [art(i, desc="") for i in range(n)]}
    (tmp_path / "news_filtered.json").write_text(json.dumps({"by_category": by_cat}), encoding="utf-8")
    monkeypatch.setattr(a2b, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(a2b, "USE_FIRESTORE", False)
    client = SummaryClient()
    monkeypatch.setattr(a2b.tracing, "make_client", lambda *a, **k: client)
    monkeypatch.setattr(a2b.tracing, "flush", lambda: None)
    monkeypatch.setattr(a2b, "fetch_article_text", lambda url: f"page text of {url} " * 20)
    return client


def _out(tmp_path):
    return json.loads((tmp_path / "news_summaries.json").read_text(encoding="utf-8"))


def test_run_default_mode_summarizes_everything(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT2B_MODE", raising=False)
    client = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(ns, "_default_select", lambda: pytest.fail("selection must not run in default mode"))
    a2b.run("t")
    out = _out(tmp_path)
    assert client.calls == 8 and out["total_summarized"] == 8 and "shortlist" not in out


def test_run_shortlist_mode_summarizes_only_the_shortlist(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT2B_MODE", "shortlist")
    client = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(ns, "_default_select", lambda: Sel(picks=2, runners=1))
    a2b.run("t")
    out = _out(tmp_path)
    assert client.calls == 3                       # 2 picks + 1 runner-up, not 8
    assert out["total_summarized"] == 3
    assert len(out["by_category"]["Safety & Alignment"]) == 3
    assert out["shortlist"]["articles_in"] == 8 and out["shortlist"]["shortlisted"] == 3
    assert all(a["summary"] for a in out["by_category"]["Safety & Alignment"])
