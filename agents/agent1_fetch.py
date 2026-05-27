 # agents/agent1_fetch.py

import anthropic
import arxiv
import io
import json
import os
import pypdf
import random
import requests
import sys
from scoring_tool import SCORING_TOOL
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MAX_FETCH, SAMPLE_SIZE, LOOKBACK_HOURS, DATA_DIR, SCORING_MODEL, MAX_TOKENS, WORD_CUTOFF


def fetch_papers():
    """Fetch AI papers submitted in the last 24 hours from ArXiv."""
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

    print(f"Found {len(papers)} papers in the last {LOOKBACK_HOURS} hours")
    return papers


def sample_papers(papers):
    """Randomly sample papers — avoids keyword popularity bias."""
    sampled = random.sample(papers, min(SAMPLE_SIZE, len(papers)))
    print(f"Sampled {len(sampled)} papers randomly")
    return sampled


def download_and_extract(pdf_url: str, paper_id: str) -> str:
    """Download a PDF from ArXiv and extract its text."""
    print(f"Downloading PDF: {paper_id}")

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
    print(f"Extracted {len(full_text)} characters from {len(reader.pages)} pages")
    return full_text


def score_paper(paper: dict, full_text: str) -> dict:
    """Score a paper using Claude on a 24-point rubric."""
    print(f"Scoring: {paper['title']}")

    # Load prompt template (no longer needs JSON structure, just instructions)
    with open("prompts/scoring_rubric.txt", "r", encoding="utf-8") as f:
        prompt_template = f.read()

    truncated_text = " ".join(full_text.split()[:WORD_CUTOFF])

    prompt = prompt_template.format(
        title=paper["title"],
        abstract=paper["abstract"],
        full_text=truncated_text
    )

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_1ST_API_KEY"))

    response = client.messages.create(
        model=SCORING_MODEL,
        max_tokens=MAX_TOKENS,
        tools=[SCORING_TOOL],
        tool_choice={"type": "tool", "name": "score_paper"},
        messages=[{"role": "user", "content": prompt}]
    )

    # Extract the tool call input directly — guaranteed to match schema
    scores = response.content[0].input
    print(f"Score: {scores.get('total', '?')}/28 — {scores.get('reasoning', '')[:80]}")
    return scores


if __name__ == "__main__":
    papers = fetch_papers()
    sampled = sample_papers(papers)

    output = {
        "fetched_at": datetime.now().isoformat(),
        "total_fetched": len(papers),
        "total_sampled": len(sampled),
        "papers": sampled
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "papers.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Saved to {out_path}")

    # Test on first paper only
    paper = sampled[0]
    print(f"\nTesting on: {paper['title']}")
    full_text = download_and_extract(paper["pdf_url"], paper["id"])
    scores = score_paper(paper, full_text)

    print("\n--- Scores ---")
    print(json.dumps(scores, indent=2))