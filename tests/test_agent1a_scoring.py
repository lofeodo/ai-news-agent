"""agent1a scoring path: stubbed Claude and PDF download, no network."""
import anthropic
import httpx

import agent1a_fetch_papers as a1a
import agent2b_summarize_news as a2b

PAPER = {"id": "http://arxiv.org/abs/2610.00001v1", "title": "T", "abstract": "the abstract", "pdf_url": "u"}


def _rate_limit():
    req = httpx.Request("POST", "http://x")
    return anthropic.RateLimitError("slow", response=httpx.Response(429, request=req), body=None)


def test_pdf_failure_scores_from_the_abstract(monkeypatch):
    seen = {}

    def boom(url, pid):
        raise RuntimeError("pdf down")

    monkeypatch.setattr(a1a, "download_and_extract", boom)
    monkeypatch.setattr(a1a, "score_paper", lambda paper, text: seen.update(text=text) or {"total": 20})
    out = a1a.process_paper(PAPER)
    assert out["scores"] == {"total": 20} and seen["text"] == "the abstract"


def test_slot_is_free_while_waiting_to_retry(monkeypatch):
    calls = []

    def score(paper, text):
        calls.append(1)
        if len(calls) == 1:
            raise _rate_limit()
        return {"total": 1}

    free_during_sleep = []

    def sleep(secs):
        got = a1a._semaphore.acquire(blocking=False)
        free_during_sleep.append(got)
        if got:
            a1a._semaphore.release()

    monkeypatch.setattr(a1a, "score_paper", score)
    monkeypatch.setattr(a1a.time, "sleep", sleep)
    assert a1a.score_with_retry(PAPER, "t") == {"total": 1}
    assert free_during_sleep == [True]


def test_news_summary_slot_is_free_while_waiting_to_retry(monkeypatch):
    free = []

    class C:
        def __init__(self):
            self.messages, self.n = self, 0

        def create(self, **kw):
            self.n += 1
            if self.n == 1:
                raise _rate_limit()
            return "ok"

    def sleep(secs):
        got = a2b._semaphore.acquire(blocking=False)
        free.append(got)
        if got:
            a2b._semaphore.release()

    monkeypatch.setattr(a2b.time, "sleep", sleep)
    assert a2b.claude_call_with_retry(C()) == "ok"
    assert free == [True]
