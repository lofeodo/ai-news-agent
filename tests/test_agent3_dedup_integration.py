"""agent3.run() with the dedup graph wired in (local mode, routing fake client, no network)."""
import json
from types import SimpleNamespace

import agent3_compose as a3
import tracing

CAT = "Industry & Business"
PAPER = {"title": "Loops", "authors": ["A"], "hf_url": "https://huggingface.co/papers/1",
         "pdf_url": "https://arxiv.org/pdf/1", "summary": "One.", "scores": {"total": 30, "upvotes": 5}}


def _article(title, lang="en", hn=None):
    return {"title": title, "url": f"https://x/{title}", "summary": f"about {title}", "language": lang, "hn_score": hn}


class RoutingClient:
    """Selection -> A,B,C with runner-up D; dedup -> flags A/B; intro -> text."""

    def __init__(self, dedup_fails=False):
        self.messages = self
        self.dedup_fails = dedup_fails
        self.dedup_calls = 0

    def create(self, **kw):
        usage = SimpleNamespace(input_tokens=10, output_tokens=5)
        names = [t["name"] for t in kw.get("tools") or []]
        if "report_duplicates" in names:
            self.dedup_calls += 1
            if self.dedup_fails:
                raise RuntimeError("dedup down")
            text = kw["messages"][-1]["content"]
            groups = [{"indices": [0, 1], "reason": "same"}] if self.dedup_calls == 1 else []
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="t", name="report_duplicates",
                                                            input={"groups": groups})], usage=usage)
        prompt = kw["messages"][0]["content"]
        if "indices" in prompt or "selected" in prompt:
            body = json.dumps({"selected": [0, 1, 2], "runners_up": [3]})
        else:
            body = "Intro text"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)], usage=usage)


def _setup(tmp_path, monkeypatch, client):
    news = {"by_category": {CAT: [_article(t) for t in "ABCD"]}}
    (tmp_path / "paper_summaries.json").write_text(json.dumps({"papers": [PAPER]}), encoding="utf-8")
    (tmp_path / "news_summaries.json").write_text(json.dumps(news), encoding="utf-8")
    monkeypatch.setattr(a3, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(a3, "USE_FIRESTORE", False)
    monkeypatch.setattr(tracing, "make_client", lambda *a, **k: client)
    monkeypatch.setattr(a3.random.Random, "shuffle", lambda self, x: None)   # keep article order stable


def test_run_dedups_and_writes_audit(tmp_path, monkeypatch):
    client = RoutingClient()
    _setup(tmp_path, monkeypatch, client)
    a3.run("r1")
    log = json.loads((tmp_path / "agent3_dedup_log.json").read_text(encoding="utf-8"))
    sec = log["sections"][0]
    assert sec["status"] == "ok" and sec["groups"][0]["removed"] and sec["fallbacks"][0]["accepted"]
    assert (tmp_path / "newsletter_0_0.html").exists()


def test_run_survives_dedup_failure(tmp_path, monkeypatch):
    client = RoutingClient(dedup_fails=True)
    _setup(tmp_path, monkeypatch, client)
    a3.run("r2")
    log = json.loads((tmp_path / "agent3_dedup_log.json").read_text(encoding="utf-8"))
    assert log["sections"][0]["status"] == "degraded"
    assert (tmp_path / "newsletter_0_0.html").exists()


def test_summary_and_run_doc_update_stay_small():
    audits = [{"status": "ok", "groups": [{"removed": [{}, {}]}], "fallbacks": [{"accepted": True}, {"accepted": False}],
               "usage": {"input_tokens": 7, "output_tokens": 3}},
              {"status": "degraded", "groups": [], "fallbacks": []}, {"status": "skipped", "groups": [], "fallbacks": []}]
    s = a3.summarize_dedup(audits)
    assert s == {"sections_checked": 2, "degraded": 1, "duplicates_removed": 2, "fallbacks_added": 1,
                 "input_tokens": 7, "output_tokens": 3}
    upd = a3.build_run_doc_update({"0_0": "h"}, "S", {}, {}, {}, s)
    assert upd["agent3_dedup_summary"] == s
    assert "agent3_dedup_summary" not in a3.build_run_doc_update({"0_0": "h"}, "S", {}, {}, {})
