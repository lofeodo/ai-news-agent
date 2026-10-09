# agents/agent1a_fetch_papers.py

import anthropic
import arxiv
import io
import json
import os
import pypdf
import random
import requests
import sys
import threading
import time
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from scoring_tool import SCORING_TOOL

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompt_guard import GUARD_PAPER_SCORING
import tracing
import spotlight_history
import trending_papers
from config import (
    MAX_FETCH, SAMPLE_SIZE, LOOKBACK_HOURS, DATA_DIR, SCORING_MODEL, MAX_TOKENS, WORD_CUTOFF,
    GCP_PROJECT_ID, TOPIC_PAPERS_SCORED, USE_FIRESTORE, PAPERS_IN_NEWSLETTER,
    TRENDING_LOOKBACK_DAYS, HF_DAILY_PAPERS_URL, TRACTION_MAX_POINTS, MAX_SCORE,
)

# Fewer usable trending candidates than this means HF (or the arXiv lookup) is misbehaving.
MIN_TRENDING_CANDIDATES = 10

# --- Rate limiting ---
MAX_CONCURRENT_CLAUDE_CALLS = 5
_semaphore = threading.Semaphore(MAX_CONCURRENT_CLAUDE_CALLS)

# Set by run() so traced Claude calls carry the pipeline run id (see tracing.usage_tags).
_TRACE_RUN_ID = None



def _arxiv_client():
    """arxiv.Client routed through the Squid proxy when configured (GCP IPs are throttled)."""
    import socket
    import urllib.request
    socket.setdefaulttimeout(30)

    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy_url:
        proxy_handler = urllib.request.ProxyHandler({
            "http": proxy_url,
            "https": proxy_url,
        })
        opener = urllib.request.build_opener(proxy_handler)
        urllib.request.install_opener(opener)
        print(f"[fetch_papers] Using proxy: {urlsplit(proxy_url).hostname}", flush=True)  # host only, the URL embeds the password

    client = arxiv.Client()

    if proxy_url:
        client._session.proxies.update({
            "http": proxy_url,
            "https": proxy_url,
        })
        print(f"[fetch_papers] Proxy set on arxiv session", flush=True)
    return client


def fetch_papers():
    """Fetch AI papers submitted in the last LOOKBACK_HOURS from ArXiv (fallback path)."""
    print(f"Fetching papers from ArXiv (last {LOOKBACK_HOURS} hours)...", flush=True)
    client = _arxiv_client()

    search = arxiv.Search(
        query="cat:cs.AI OR cat:cs.LG",
        max_results=MAX_FETCH,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending
    )

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    papers = []
    for result in client.results(search):
        if result.published < cutoff:
            break
        papers.append({
            "id": result.entry_id,
            "title": result.title,
            "abstract": result.summary,
            "authors": [a.name for a in result.authors[:5]],
            "published": result.published.isoformat(),
            "pdf_url": result.pdf_url,
            "categories": result.categories
        })
        if len(papers) % 100 == 0:
            print(f"  [arxiv]    fetched {len(papers)} papers so far...", flush=True)

    print(f"Found {len(papers)} papers in the last {LOOKBACK_HOURS} hours", flush=True)
    return papers


def select_trending_papers(run_id: str):
    """The SAMPLE_SIZE most-upvoted HF Daily Papers of the last TRENDING_LOOKBACK_DAYS that no earlier
    run spotlighted. Returns papers carrying `upvotes` and `community_traction`; raises if HF or the
    arXiv lookup fails or yields too few papers (the caller falls back to the random sample)."""
    seen = spotlight_history.load_seen_ids(USE_FIRESTORE, GCP_PROJECT_ID, DATA_DIR, run_id)
    print(f"[trending] {len(seen)} papers spotlighted before", flush=True)

    candidates = trending_papers.fetch_hf_candidates(HF_DAILY_PAPERS_URL, TRENDING_LOOKBACK_DAYS)
    print(f"[trending] {len(candidates)} HF papers in the last {TRENDING_LOOKBACK_DAYS} days", flush=True)

    # Over-fetch: some HF papers are not cs.AI/cs.LG and get dropped after the arXiv lookup.
    ranked = trending_papers.rank_candidates(candidates, seen, SAMPLE_SIZE * 2)
    upvotes = {c["arxiv_id"]: c["upvotes"] for c in ranked}
    papers = trending_papers.keep_ai_papers(
        trending_papers.fetch_arxiv_papers([c["arxiv_id"] for c in ranked], _arxiv_client()))
    papers.sort(key=lambda p: -upvotes.get(trending_papers.base_arxiv_id(p["id"]), 0))
    papers = papers[:SAMPLE_SIZE]
    if len(papers) < MIN_TRENDING_CANDIDATES:
        raise RuntimeError(f"only {len(papers)} usable trending papers (need {MIN_TRENDING_CANDIDATES})")
    return trending_papers.attach_traction(papers, upvotes, TRACTION_MAX_POINTS)


def sample_papers(papers):
    """Randomly sample papers — avoids keyword popularity bias."""
    sampled = random.sample(papers, min(SAMPLE_SIZE, len(papers)))
    print(f"Sampled {len(sampled)} papers randomly")
    return sampled


def download_and_extract(pdf_url: str, paper_id: str) -> str:
    """Download a PDF from ArXiv and extract its text."""
    print(f"  [download] {paper_id}")

    if not pdf_url or not pdf_url.startswith(("https://", "http://")):
        raise ValueError(f"Unsafe URL scheme for paper {paper_id}: {pdf_url!r}")

    headers = {"User-Agent": "ai-news-agent/1.0 (research project)"}
    response = requests.get(pdf_url, headers=headers, timeout=30)
    response.raise_for_status()

    pdf_file = io.BytesIO(response.content)
    reader = pypdf.PdfReader(pdf_file)

    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)

    full_text = "\n".join(pages)
    return full_text


def score_paper(paper: dict, full_text: str) -> dict:
    """Score a paper using Claude on an 8-dimension rubric plus community traction."""
    with open("prompts/scoring_rubric.txt", "r", encoding="utf-8") as f:
        prompt_template = f.read()

    truncated_text = " ".join(full_text.split()[:WORD_CUTOFF])

    upvotes = paper.get("upvotes")
    upvote_line = (f"{upvotes} upvotes on Hugging Face Daily Papers" if upvotes is not None
                   else "not available")
    prompt = prompt_template.format(
        upvote_line=upvote_line,
        title=paper["title"],
        abstract=paper["abstract"],
        full_text=truncated_text
    )

    client = tracing.make_client("agent1a", _TRACE_RUN_ID, timeout=60.0)

    print(f"  [api-call-start] {paper['title'][:40]}", flush=True)
    response = client.messages.create(
        model=SCORING_MODEL,
        max_tokens=MAX_TOKENS,
        system=GUARD_PAPER_SCORING,
        tools=[SCORING_TOOL],
        tool_choice={"type": "tool", "name": "score_paper"},
        messages=[{"role": "user", "content": prompt}]
    )
    print(f"  [api-call-done] {paper['title'][:40]}", flush=True)

    if not response.content:
        raise RuntimeError(f"Empty Claude response for paper: {paper['title'][:40]}")
    return finalize_scores(response.content[0].input, paper)


_CLAUDE_DIMENSIONS = (
    "novelty", "rigor", "reproducibility", "clarity", "practical_applicability",
    "significance", "disruption_potential", "wow_factor",
)


def finalize_scores(scores: dict, paper: dict) -> dict:
    """Recompute the total in code: Claude's 8 dimensions (max 33) + community traction (max 8).

    The model's own `total` is not trusted; traction comes from HF upvotes, never from the model."""
    claude_total = sum(int(scores.get(d, 0)) for d in _CLAUDE_DIMENSIONS)
    traction = int(paper.get("community_traction", 0))
    return {**scores, "claude_total": claude_total, "community_traction": traction,
            "upvotes": paper.get("upvotes"), "total": claude_total + traction}


def score_with_retry(paper: dict, full_text: str, max_retries: int = 3) -> dict:
    """Score with exponential backoff on rate limit errors."""
    for attempt in range(max_retries):
        try:
            return score_paper(paper, full_text)
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt * 10  # 10s, 20s, 40s
            print(f"  [retry]    rate limited, waiting {wait}s...")
            time.sleep(wait)


def process_paper(paper: dict) -> dict:
    """Download, extract, and score a single paper. Returns merged result."""
    paper_id = paper["id"].split("/")[-1]
    try:
        full_text = download_and_extract(paper["pdf_url"], paper_id)

        print(f"  [semaphore-wait] {paper_id}", flush=True)
        with _semaphore:
            print(f"  [scoring]  {paper['title'][:60]}...", flush=True)
            scores = score_with_retry(paper, full_text)
            print(f"  [scoring-done] {paper_id}", flush=True)

        print(f"  [done]     {paper['title'][:50]} → {scores.get('total', '?')}/{MAX_SCORE}", flush=True)
        return {**paper, "scores": scores, "error": None}

    except Exception as e:
        print(f"  [error]    {paper_id}: {e}", flush=True)
        return {**paper, "scores": None, "error": str(e)}


def score_all_papers(papers: list) -> list:
    """Score all papers concurrently. Returns list sorted by score descending."""
    print(f"\nScoring {len(papers)} papers with up to {MAX_CONCURRENT_CLAUDE_CALLS} concurrent Claude calls...\n")
    results = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_paper = {executor.submit(process_paper, p): p for p in papers}

        for future in as_completed(future_to_paper):
            result = future.result()
            results.append(result)

    results.sort(key=lambda r: r["scores"]["total"] if r["scores"] else -1, reverse=True)
    return results


def _record_failure(run_id: str, agent_name: str, error: Exception) -> None:
    """Best-effort write of a top-level run failure to Firestore, for health checks."""
    if not USE_FIRESTORE:
        return
    try:
        from google.cloud import firestore
        firestore.Client(project=GCP_PROJECT_ID).collection("pipeline_runs").document(run_id).set(
            {
                f"{agent_name}_error": str(error),
                f"{agent_name}_failed_at": datetime.now(timezone.utc).isoformat(),
            },
            merge=True
        )
        print(f"[{agent_name}]  Recorded failure to Firestore (run_id={run_id})", flush=True)
    except Exception as record_error:
        print(f"[{agent_name}]  Failed to record failure to Firestore: {record_error}", flush=True)


def run(run_id: str):
    """Main agent logic. Called by main.py (Cloud Run) or orchestrator.py."""
    global _TRACE_RUN_ID
    _TRACE_RUN_ID = run_id
    start_time = datetime.now()

    try:
        try:
            sampled = select_trending_papers(run_id)
            print(f"[trending] scoring the {len(sampled)} most-upvoted unspotlighted papers", flush=True)
        except Exception as e:
            print(f"[trending] falling back to a random sample of recent ArXiv papers: {e}", flush=True)
            sampled = sample_papers(fetch_papers())
        sampled = [{**p, "hf_url": trending_papers.hf_paper_url(p["id"])} for p in sampled]
        scored = score_all_papers(sampled)

        successful = [r for r in scored if r["scores"] is not None]
        failed = [r for r in scored if r["scores"] is None]
        top_papers = successful[:PAPERS_IN_NEWSLETTER]

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n--- Done in {elapsed:.1f}s ---")
        print(f"Scored: {len(successful)}/{len(sampled)} papers successfully")
        if failed:
            print(f"Failed: {len(failed)} papers")

        print(f"\n=== TOP {PAPERS_IN_NEWSLETTER} PAPERS ===")
        for i, paper in enumerate(top_papers, 1):
            scores = paper.get("scores") or {}
            print(f"{i}. [{scores.get('total', '?')}/{MAX_SCORE}] {paper['title']}")
            print(f"   {scores.get('reasoning', '')}\n")

        if spotlight_history.is_debug_run(USE_FIRESTORE, GCP_PROJECT_ID, run_id):
            print("[spotlight] debug run: not recording the spotlight, so a release run can still pick these papers",
                  flush=True)
        else:
            recorded = spotlight_history.record_spotlight(
                [{"arxiv_id": trending_papers.base_arxiv_id(p["id"]), "title": p["title"]} for p in top_papers],
                run_id, USE_FIRESTORE, GCP_PROJECT_ID, DATA_DIR)
            print(f"[spotlight] recorded {recorded} spotlighted paper(s)", flush=True)

        os.makedirs(DATA_DIR, exist_ok=True)
        out_path = os.path.join(DATA_DIR, "scored_papers.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "run_at": start_time.isoformat(),
                "elapsed_seconds": elapsed,
                "total_sampled": len(sampled),
                "total_scored": len(successful),
                "total_failed": len(failed),
                "top_papers": top_papers,
                "all_scored": scored
            }, f, indent=2, ensure_ascii=False)

        print(f"Saved full results to {out_path}")

        if USE_FIRESTORE:
            from google.cloud import firestore, pubsub_v1
            db  = firestore.Client(project=GCP_PROJECT_ID)
            db.collection("pipeline_runs").document(run_id).set(
                {
                    "scored_papers": top_papers,
                    # Aware UTC, not start_time.isoformat() — start_time is a
                    # naive local() timestamp used for elapsed-time math below;
                    # writing it here used to overwrite the orchestrator's
                    # aware started_at with a naive one, which crashed
                    # agent_healthcheck's age check every week.
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "run_id": run_id,
                },
                merge=True
            )
            print(f"[agent1a]  Saved scored_papers to Firestore (run_id={run_id})")

            publisher  = pubsub_v1.PublisherClient()
            topic_path = publisher.topic_path(GCP_PROJECT_ID, TOPIC_PAPERS_SCORED)
            data       = json.dumps({"run_id": run_id}).encode("utf-8")
            publisher.publish(topic_path, data).result(timeout=30)
            print(f"[agent1a]  Published to {TOPIC_PAPERS_SCORED} (run_id={run_id})")
    except Exception as e:
        _record_failure(run_id, "agent1a", e)
        raise
    finally:
        tracing.flush()


if __name__ == "__main__":
    run(run_id="local-debug")