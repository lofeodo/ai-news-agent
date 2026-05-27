 # agents/agent1_fetch.py

import arxiv
import random
import json
import requests
import pypdf
import io
from datetime import datetime, timedelta, timezone
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MAX_FETCH, SAMPLE_SIZE, LOOKBACK_HOURS, DATA_DIR


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

    # Test PDF extraction on first paper
    paper = sampled[0]
    print(f"\nTesting PDF extraction on: {paper['title']}")
    text = download_and_extract(paper["pdf_url"], paper["id"])
    print("\n--- Extracted text preview ---")
    print(text[:500])