# agents/agent2a_summarize_papers.py

import anthropic
import io
import json
import os
import requests
import sys
import threading
import time
from datetime import datetime

import pypdf

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    DATA_DIR, SCORING_MODEL, PAPER_SUMMARY_MAX_TOKENS, WORD_CUTOFF,
    GCP_PROJECT_ID, TOPIC_CONTENT_SUMMARIZED, FIRESTORE_COLLECTION, USE_FIRESTORE,
)

# --- Rate limiting ---
MAX_CONCURRENT_CLAUDE_CALLS = 5
_semaphore = threading.Semaphore(MAX_CONCURRENT_CLAUDE_CALLS)


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------

def download_and_extract(pdf_url: str, paper_id: str) -> str | None:
    """Download a PDF and extract its text. Returns None on failure."""
    print(f"  [pdf]      {paper_id}")
    try:
        headers = {"User-Agent": "ai-news-agent/1.0 (research project)"}
        response = requests.get(pdf_url, headers=headers, timeout=30)
        response.raise_for_status()

        reader = pypdf.PdfReader(io.BytesIO(response.content))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)

        full_text = "\n".join(pages)
        truncated = " ".join(full_text.split()[:WORD_CUTOFF])
        return truncated
    except Exception as e:
        print(f"  [pdf]      failed for {paper_id}: {e}")
        return None


def fallback_text(paper: dict) -> str:
    """Build fallback text from abstract + scoring reasoning when PDF fetch fails."""
    parts = []
    if paper.get("abstract"):
        parts.append(f"Abstract:\n{paper['abstract']}")
    reasoning = (paper.get("scores") or {}).get("reasoning", "")
    if reasoning:
        parts.append(f"Scoring notes:\n{reasoning}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Claude call
# ---------------------------------------------------------------------------

def claude_call_with_retry(client: anthropic.Anthropic, max_retries: int = 4, **kwargs) -> object:
    """Call client.messages.create with exponential backoff on rate limit errors."""
    for attempt in range(max_retries):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            wait = 10 * (2 ** attempt)
            print(f"  [retry]    rate limited, waiting {wait}s...")
            time.sleep(wait)


def summarize_paper(paper: dict, text: str, prompt_template: str, client: anthropic.Anthropic) -> str:
    """Ask Claude for a 4-paragraph review of one paper."""
    prompt = prompt_template.format(title=paper["title"], text=text)

    with _semaphore:
        response = claude_call_with_retry(
            client,
            model=SCORING_MODEL,
            max_tokens=PAPER_SUMMARY_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}]
        )

    if not response.content:
        raise RuntimeError("Empty Claude response content")
    return response.content[0].text.strip()


def validate_summary(summary: str, paper_id: str) -> bool:
    """Warn if Claude didn't return 2 paragraphs."""
    paragraphs = [p.strip() for p in summary.split("\n\n") if p.strip()]
    if len(paragraphs) < 2:
        print(f"  [validate] {paper_id}: expected 2 paragraphs, got {len(paragraphs)} — storing anyway")
        return False
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def increment_and_check(run_id: str) -> bool:
    """
    Atomically increment agent2_completions in Firestore using a transaction.
    Returns True if this agent pushed the count to 2 (both agent2a and agent2b done).
    """
    from google.cloud import firestore

    db  = firestore.Client(project=GCP_PROJECT_ID)
    ref = db.collection(FIRESTORE_COLLECTION).document(run_id)

    @firestore.transactional
    def _increment(transaction, ref):
        snapshot = ref.get(transaction=transaction)
        data     = snapshot.to_dict() or {}
        new_val  = data.get("agent2_completions", 0) + 1
        transaction.update(ref, {"agent2_completions": new_val})
        return new_val

    count = _increment(db.transaction(), ref)
    print(f"[agent2a]  agent2_completions = {count}")
    return count >= 2


def run(run_id: str):
    """Main agent logic. Called by main.py (Cloud Run) or orchestrator.py."""
    start_time = datetime.now()

    with open("prompts/paper_summary_prompt.txt", "r", encoding="utf-8") as f:
        prompt_template = f.read()

    if USE_FIRESTORE:
        from google.cloud import firestore as _fs
        doc_snap = _fs.Client(project=GCP_PROJECT_ID).collection(FIRESTORE_COLLECTION).document(run_id).get()
        doc      = doc_snap.to_dict()
        if not doc:
            raise RuntimeError(f"[agent2a] Firestore document not found for run_id={run_id}")
        papers   = doc.get("scored_papers")
        if papers is None:
            raise RuntimeError(f"[agent2a] 'scored_papers' missing from Firestore document run_id={run_id}")
        print(f"[agent2a]  Loaded {len(papers)} papers from Firestore")
    else:
        in_path = os.path.join(DATA_DIR, "scored_papers.json")
        with open(in_path, "r", encoding="utf-8") as f:
            scored = json.load(f)
        papers = scored["top_papers"]
    print(f"Summarizing {len(papers)} papers...\n")

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_1ST_API_KEY"))
    results = []

    for i, paper in enumerate(papers, 1):
        paper_id = paper["id"].split("/")[-1]
        print(f"[{i}/{len(papers)}] {paper['title'][:80]}...")

        text = download_and_extract(paper["pdf_url"], paper_id)
        used_fallback = text is None
        if used_fallback:
            print(f"  [pdf]      using abstract+reasoning fallback")
            text = fallback_text(paper)

        try:
            summary = summarize_paper(paper, text, prompt_template, client)
        except Exception as e:
            print(f"  [error]    {paper_id}: {e}")
            results.append({**paper, "summary": None, "used_fallback": used_fallback, "summary_error": str(e)})
            continue

        validate_summary(summary, paper_id)

        status = "fallback" if used_fallback else "full PDF"
        print(f"  [done]     ({status})")

        results.append({
            "id":            paper["id"],
            "title":         paper["title"],
            "authors":       paper["authors"],
            "published":     paper["published"],
            "pdf_url":       paper["pdf_url"],
            "categories":    paper["categories"],
            "scores":        paper["scores"],
            "summary":       summary,
            "used_fallback": used_fallback,
            "summary_error": None,
        })

    elapsed = (datetime.now() - start_time).total_seconds()
    successful = [r for r in results if r["summary"]]
    failed     = [r for r in results if not r["summary"]]

    print(f"\n--- Done in {elapsed:.1f}s ---")
    print(f"Summarized: {len(successful)}/{len(papers)} papers")
    if failed:
        print(f"Failed: {len(failed)} papers")

    if USE_FIRESTORE:
        from google.cloud import firestore as _fs
        _fs.Client(project=GCP_PROJECT_ID).collection(FIRESTORE_COLLECTION).document(run_id).update({
            "paper_summaries": results
        })
        print(f"[agent2a]  Saved paper_summaries to Firestore (run_id={run_id})")
    else:
        os.makedirs(DATA_DIR, exist_ok=True)
        out_path = os.path.join(DATA_DIR, "paper_summaries.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "run_at":           start_time.isoformat(),
                "elapsed_seconds":  elapsed,
                "total_papers":     len(papers),
                "total_summarized": len(successful),
                "total_failed":     len(failed),
                "papers":           results,
            }, f, indent=2, ensure_ascii=False)
        print(f"Saved to {out_path}")

    if USE_FIRESTORE:
        should_trigger = increment_and_check(run_id)
        if should_trigger:
            from google.cloud import pubsub_v1
            publisher  = pubsub_v1.PublisherClient()
            topic_path = publisher.topic_path(GCP_PROJECT_ID, TOPIC_CONTENT_SUMMARIZED)
            data       = json.dumps({"run_id": run_id}).encode("utf-8")
            publisher.publish(topic_path, data).result(timeout=30)
            print(f"[agent2a]  Both agent2s done — published to {TOPIC_CONTENT_SUMMARIZED} (run_id={run_id})")
        else:
            print(f"[agent2a]  Waiting for agent2b to finish before triggering agent3")


if __name__ == "__main__":
    run(run_id="local-debug")