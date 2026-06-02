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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from scoring_tool import SCORING_TOOL

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    MAX_FETCH, SAMPLE_SIZE, LOOKBACK_HOURS, DATA_DIR, SCORING_MODEL, MAX_TOKENS, WORD_CUTOFF,
    GCP_PROJECT_ID, TOPIC_PAPERS_SCORED, USE_FIRESTORE,
)

# --- Rate limiting ---
MAX_CONCURRENT_CLAUDE_CALLS = 5
_semaphore = threading.Semaphore(MAX_CONCURRENT_CLAUDE_CALLS)


def fetch_papers():
    """Fetch AI papers submitted in the last LOOKBACK_HOURS from ArXiv."""
    print(f"Fetching papers from ArXiv (last {LOOKBACK_HOURS} hours)...")

    client = arxiv.Client()

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
        if len(papers) % 10 == 0:
            print(f"  [arxiv]    fetched {len(papers)} papers so far...", flush=True)

    print(f"Found {len(papers)} papers in the last {LOOKBACK_HOURS} hours", flush=True)
    return papers


def sample_papers(papers):
    """Randomly sample papers — avoids keyword popularity bias."""
    sampled = random.sample(papers, min(SAMPLE_SIZE, len(papers)))
    print(f"Sampled {len(sampled)} papers randomly")
    return sampled


def download_and_extract(pdf_url: str, paper_id: str) -> str:
    """Download a PDF from ArXiv and extract its text."""
    print(f"  [download] {paper_id}")

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
    """Score a paper using Claude on a 28-point rubric."""
    with open("prompts/scoring_rubric.txt", "r", encoding="utf-8") as f:
        prompt_template = f.read()

    truncated_text = " ".join(full_text.split()[:WORD_CUTOFF])

    prompt = prompt_template.format(
        title=paper["title"],
        abstract=paper["abstract"],
        full_text=truncated_text
    )

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_1ST_API_KEY"), timeout=60.0)

    response = client.messages.create(
        model=SCORING_MODEL,
        max_tokens=MAX_TOKENS,
        tools=[SCORING_TOOL],
        tool_choice={"type": "tool", "name": "score_paper"},
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].input


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

        with _semaphore:
            print(f"  [scoring]  {paper['title'][:60]}...")
            scores = score_with_retry(paper, full_text)

        print(f"  [done]     {paper['title'][:50]} → {scores.get('total', '?')}/28")
        return {**paper, "scores": scores, "error": None}

    except Exception as e:
        print(f"  [error]    {paper_id}: {e}")
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


def run(run_id: str):
    """Main agent logic. Called by main.py (Cloud Run) or orchestrator.py."""
    start_time = datetime.now()

    papers = fetch_papers()
    sampled = sample_papers(papers)
    scored = score_all_papers(sampled)

    successful = [r for r in scored if r["scores"] is not None]
    failed = [r for r in scored if r["scores"] is None]
    top_5 = successful[:5]

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n--- Done in {elapsed:.1f}s ---")
    print(f"Scored: {len(successful)}/{len(sampled)} papers successfully")
    if failed:
        print(f"Failed: {len(failed)} papers")

    print("\n=== TOP 5 PAPERS ===")
    for i, paper in enumerate(top_5, 1):
        print(f"{i}. [{paper['scores']['total']}/28] {paper['title']}")
        print(f"   {paper['scores']['reasoning']}\n")

    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "scored_papers.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "run_at": start_time.isoformat(),
            "elapsed_seconds": elapsed,
            "total_sampled": len(sampled),
            "total_scored": len(successful),
            "total_failed": len(failed),
            "top_5": top_5,
            "all_scored": scored
        }, f, indent=2, ensure_ascii=False)

    print(f"Saved full results to {out_path}")

    if USE_FIRESTORE:
        from google.cloud import pubsub_v1
        publisher  = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(GCP_PROJECT_ID, TOPIC_PAPERS_SCORED)
        data       = json.dumps({"run_id": run_id}).encode("utf-8")
        publisher.publish(topic_path, data).result()
        print(f"[agent1a]  Published to {TOPIC_PAPERS_SCORED} (run_id={run_id})")


if __name__ == "__main__":
    run(run_id="local-debug")