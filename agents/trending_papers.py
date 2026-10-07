# agents/trending_papers.py
#
# Picks the week's candidate papers by community attention instead of at random: the most-upvoted
# Hugging Face Daily Papers of the last TRENDING_LOOKBACK_DAYS. arXiv's API has no popularity or
# citation data, and citations of a two-week-old paper are mostly zero, so HF upvotes are the signal.
# The ranking helpers are pure functions (no network); fetch_* do the I/O and raise on failure so the
# caller can fall back to the old random-sample path.

import re
from datetime import datetime, timedelta, timezone

import requests

HF_TIMEOUT = 30
_ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}$")


def base_arxiv_id(identifier: str) -> str:
    """'http://arxiv.org/abs/2610.07557v2' or '2610.07557v2' -> '2610.07557'."""
    ident = (identifier or "").rstrip("/").split("/")[-1]
    return re.sub(r"v\d+$", "", ident)


def hf_paper_url(identifier: str) -> str:
    """The Hugging Face page for a paper; the newsletter links here instead of arXiv."""
    return f"https://huggingface.co/papers/{base_arxiv_id(identifier)}"


def fetch_hf_candidates(url: str, lookback_days: int, now: datetime | None = None, session=None) -> list[dict]:
    """Return [{arxiv_id, title, upvotes}] for HF Daily Papers published in the window (deduped).

    The endpoint returns one day per `date` query; walk back day by day. Raises on any HTTP error.
    """
    now = now or datetime.now(timezone.utc)
    get = (session or requests).get
    found: dict[str, dict] = {}
    for offset in range(lookback_days + 1):
        day = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
        resp = get(url, params={"date": day, "limit": 100}, timeout=HF_TIMEOUT,
                   headers={"User-Agent": "ai-news-agent/1.0 (research project)"})
        resp.raise_for_status()
        for item in resp.json():
            paper = item.get("paper") or {}
            arxiv_id = base_arxiv_id(paper.get("id", ""))
            if not _ARXIV_ID.match(arxiv_id):
                continue
            upvotes = int(paper.get("upvotes") or 0)
            prev = found.get(arxiv_id)
            if prev is None or upvotes > prev["upvotes"]:
                found[arxiv_id] = {"arxiv_id": arxiv_id, "title": paper.get("title", ""), "upvotes": upvotes}
    return list(found.values())


def rank_candidates(candidates: list[dict], seen_ids: set[str], limit: int) -> list[dict]:
    """Drop already-spotlighted papers, then take the `limit` most upvoted.

    Filtering before truncating is what makes the pool refill from the next-ranked papers.
    """
    fresh = [c for c in candidates if c["arxiv_id"] not in seen_ids]
    fresh.sort(key=lambda c: (-c["upvotes"], c["arxiv_id"]))
    return fresh[:limit]


def traction_points(upvotes: int, all_upvotes: list[int], max_points: int) -> int:
    """1..max_points from the paper's upvote percentile within the shortlist (ties share the lower rank)."""
    if len(all_upvotes) <= 1:
        return max_points
    below = sum(1 for u in all_upvotes if u < upvotes)
    pct = below / (len(all_upvotes) - 1)  # 0.0 (least upvoted) .. 1.0 (most)
    return 1 + round(pct * (max_points - 1))


def fetch_arxiv_papers(arxiv_ids: list[str], client) -> list[dict]:
    """Look up abstract/authors/pdf_url for arXiv ids, in the dict shape fetch_papers() returns."""
    import arxiv
    papers = []
    for i in range(0, len(arxiv_ids), 50):
        search = arxiv.Search(id_list=arxiv_ids[i:i + 50], max_results=50)
        for r in client.results(search):
            papers.append({
                "id": r.entry_id,
                "title": r.title,
                "abstract": r.summary,
                "authors": [a.name for a in r.authors[:5]],
                "published": r.published.isoformat(),
                "pdf_url": r.pdf_url,
                "categories": r.categories,
            })
    return papers


def keep_ai_papers(papers: list[dict]) -> list[dict]:
    return [p for p in papers if {"cs.AI", "cs.LG"} & set(p.get("categories") or [])]


def attach_traction(papers: list[dict], upvotes_by_id: dict[str, int], max_points: int) -> list[dict]:
    """Add `upvotes` and `community_traction` to each paper (new dicts)."""
    ups = [upvotes_by_id.get(base_arxiv_id(p["id"]), 0) for p in papers]
    out = []
    for p, u in zip(papers, ups):
        out.append({**p, "upvotes": u, "community_traction": traction_points(u, ups, max_points)})
    return out
