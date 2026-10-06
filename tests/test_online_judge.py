"""Weekly judge: selection, caps, idempotence, drift. Stub client and fake Firestore only."""
from types import SimpleNamespace

import pytest

import drift
import online_judge as oj
import summary_sources as ss
from fakes_firestore import FakeDb
from test_summary_sources import Db


def verdict(ok):
    return SimpleNamespace(type="tool_use", name="record_verdict", input={"supported": ok, "unsupported_claims": [] if ok else ["x"]})


class Client:
    def __init__(self, ok=lambda n: True, fail_on=()):
        self.messages = self
        self.n = 0
        self.ok, self.fail_on = ok, set(fail_on)

    def create(self, **kw):
        self.n += 1
        if self.n in self.fail_on:
            raise RuntimeError("boom")
        return SimpleNamespace(content=[verdict(self.ok(self.n))], usage=SimpleNamespace(input_tokens=1000, output_tokens=50))


def make_doc(n_news=20, n_fallback=4, n_papers=3):
    news = [{"url": f"http://n/{i}", "title": f"T{i}", "summary": f"S{i}", "used_fallback": i < n_fallback} for i in range(n_news)]
    return {"paper_summaries": [{"id": f"p{i}", "title": "P", "summary": "ps", "used_fallback": False} for i in range(n_papers)],
            "news_summaries": {"A": news[: n_news // 2], "B": news[n_news // 2:]}}


def seed_sources(db, run_id, doc, words=50):
    ss.save_sources(db, run_id, "paper", [{"ident": p["id"], "text": "w " * words} for p in doc["paper_summaries"]])
    arts = [a for v in doc["news_summaries"].values() for a in v]
    ss.save_sources(db, run_id, "news", [{"ident": a["url"], "title": a["title"], "text": "w " * words} for a in arts])


def test_select_papers_first_capped_and_deterministic():
    doc = make_doc()
    a = oj.select_items(doc, "r1", 12)
    assert len(a) == 12 and [i["kind"] for i in a[:3]] == ["paper"] * 3
    assert a == oj.select_items(doc, "r1", 12)
    assert a != oj.select_items(doc, "r2", 12)


def test_select_includes_fallback_stratum():
    picked = oj.select_items(make_doc(n_news=40, n_fallback=4), "r1", 12)
    assert sum(1 for i in picked if i["kind"] == "news" and i["used_fallback"]) >= 1
    assert len(oj.select_items(make_doc(n_news=2, n_fallback=0, n_papers=1), "r", 12)) == 3


def test_judge_run_stores_counts_without_text_and_is_idempotent():
    db, doc = Db(), make_doc()
    seed_sources(db, "r1", doc)
    c = Client(ok=lambda n: n % 4 != 0)
    res = oj.judge_run(db, c, "r1", doc)
    assert res["judged"] == 12 and res["unsupported"] == 3 and 0 < res["unsupported_ci_high"] < 1
    stored = db.store["pipeline_runs"]["r1"]["judge_results"]
    assert "w w" not in str(stored) and "ps" not in [i.get("summary") for i in stored["items"]]
    assert all(set(i) <= {"kind", "ident", "used_fallback", "status", "supported", "n_unsupported_claims", "error"} for i in stored["items"])
    calls = c.n
    again = oj.judge_run(db, c, "r1", {**doc, "judge_results": stored})
    assert again == stored and c.n == calls  # no second spend


def test_cost_cap_skips_before_any_call():
    db, doc = Db(), make_doc()
    seed_sources(db, "r1", doc, words=5000)
    c = Client()
    res = oj.judge_run(db, c, "r1", doc, max_usd=0.01)
    assert res["skipped"] == "cost_cap" and c.n == 0 and res["judged"] == 0


def test_default_cap_admits_a_full_worst_case_sample():
    db, doc = Db(), make_doc()
    seed_sources(db, "r1", doc, words=5000)
    res = oj.judge_run(db, Client(), "r1", doc)
    assert "skipped" not in res and res["judged"] == 12


def test_missing_source_and_call_errors_are_counted_not_raised():
    db, doc = Db(), make_doc(n_papers=1, n_news=4, n_fallback=0)
    seed_sources(db, "r1", doc)
    db.store["summary_sources"].pop(ss.source_key("r1", "paper", "p0"))
    res = oj.judge_run(db, Client(fail_on={1}), "r1", doc)
    assert res["no_source"] == 1 and res["errors"] == 1 and res["judged"] == 3


def test_drift_needs_history_and_flags_big_jump():
    base = [{"judged": 12, "unsupported": 1}] * 4
    assert drift.evaluate_judge({"judged": 12, "unsupported": 9}, base[:2], min_runs=3, p_threshold=0.01, min_shift=0.15)["status"] == "insufficient_history"
    assert drift.evaluate_judge({"judged": 12, "unsupported": 11}, base, min_runs=3, p_threshold=0.01, min_shift=0.15)["status"] == "drift"
    assert drift.evaluate_judge({"judged": 12, "unsupported": 3}, base, min_runs=3, p_threshold=0.01, min_shift=0.15)["status"] == "ok"
    assert drift.evaluate_judge({"judged": 12, "unsupported": 0}, base, min_runs=3, p_threshold=0.01, min_shift=0.15)["status"] == "ok"
