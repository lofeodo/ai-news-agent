"""Generate blank labeling templates from frozen eval inputs.

The label columns (`gold_category`, `supported`) are always written empty: gold labels are
written by hand by the repo owner, never by a script.

CMD:  python -m evals.make_label_templates articles
      python -m evals.make_label_templates summaries   (re-fetches source text; needs network)
"""
import csv
import random
import sys
from pathlib import Path

from evals.snapshot import FIXTURES_DIR, PRIVATE_DIR, article_id, save_private_text

LABELS_DIR = Path(__file__).parent / "labels"
LOW_CONFIDENCE_BELOW = 4  # matches agent1b's REVIEW_CONFIDENCE_THRESHOLD default

ARTICLE_COLUMNS = ["id", "url", "title", "snippet", "first_pass_category", "confidence",
                   "sample_stratum", "gold_category"]


def build_article_template(articles, n=100, n_low=40, seed=0):
    """Seeded sample of n articles, with low-confidence ones over-represented (up to n_low).

    Rows are shuffled so stratum isn't recoverable from position. gold_category is always "".
    """
    rng = random.Random(seed)
    usable = [a for a in articles if a.get("confidence") is not None and a.get("first_pass_category")]
    low = sorted((a for a in usable if a["confidence"] < LOW_CONFIDENCE_BELOW), key=lambda a: a["id"])
    high = sorted((a for a in usable if a["confidence"] >= LOW_CONFIDENCE_BELOW), key=lambda a: a["id"])
    picked_low = rng.sample(low, min(n_low, len(low)))
    picked_high = rng.sample(high, min(n - len(picked_low), len(high)))
    rows = [(a, "low_confidence") for a in picked_low] + [(a, "high_confidence") for a in picked_high]
    rng.shuffle(rows)
    return [{
        "id": a["id"], "url": a["url"], "title": a["title"], "snippet": a["snippet"],
        "first_pass_category": a["first_pass_category"], "confidence": a["confidence"],
        "sample_stratum": stratum, "gold_category": "",
    } for a, stratum in rows]


SUMMARY_COLUMNS = ["id", "kind", "title", "url", "source_kind", "source_text_ref",
                   "generated_summary", "used_fallback", "supported"]


def _is_twitter(url):
    return "twitter.com" in url or "x.com/" in url


def build_summary_template(news, papers, fetch_news_text, fetch_paper_text, n_full=25, n_fallback=5,
                           n_papers=10, seed=0, private_dir=PRIVATE_DIR):
    """Seeded sample of generated summaries plus the source text each was written from.

    News: n_full summaries written from fetched article text (re-fetched here; rows whose fetch fails
    are skipped and replaced) and n_fallback written from the description only (source = description).
    Papers: n_papers summaries written from PDF text. Full text goes to the gitignored private dir;
    the CSV only references it. `supported` is always "". Returns (rows, skipped_fetch_failures).
    """
    rng = random.Random(seed)
    skipped = 0
    rows = []

    def row(kind, item_id, title, url, source_kind, text, summary, used_fallback):
        path = save_private_text("summary_sources", item_id, text, private_dir)
        return {"id": item_id, "kind": kind, "title": title, "url": url, "source_kind": source_kind,
                "source_text_ref": f"fixtures/private/summary_sources/{path.name}",
                "generated_summary": summary, "used_fallback": used_fallback, "supported": ""}

    usable = [a for a in news if a.get("summary") and a.get("url") and not _is_twitter(a["url"])]
    full = sorted((a for a in usable if not a.get("used_fallback")), key=lambda a: a["url"])
    fall = sorted((a for a in usable if a.get("used_fallback") and (a.get("description") or "").strip()),
                  key=lambda a: a["url"])
    rng.shuffle(full)
    rng.shuffle(fall)
    got = 0
    for a in full:
        if got == n_full:
            break
        text = fetch_news_text(a["url"])
        if not text:
            skipped += 1
            continue
        rows.append(row("news", article_id(a["url"]), a["title"], a["url"], "full_text", text, a["summary"], False))
        got += 1
    for a in fall[:n_fallback]:
        rows.append(row("news", article_id(a["url"]), a["title"], a["url"], "description",
                        a["description"], a["summary"], True))

    cands = sorted((p for p in papers if p.get("summary") and not p.get("used_fallback")), key=lambda p: p["id"])
    rng.shuffle(cands)
    got = 0
    for p in cands:
        if got == n_papers:
            break
        text = fetch_paper_text(p["pdf_url"], p["id"])
        if not text:
            skipped += 1
            continue
        rows.append(row("paper", article_id(p["id"]), p["title"], p["pdf_url"], "pdf_text", text, p["summary"], False))
        got += 1

    rng.shuffle(rows)
    return rows, skipped


def write_csv(path, columns, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def make_article_template(frozen_path=None, out_path=None, **kwargs):
    import json
    frozen = json.loads(Path(frozen_path or FIXTURES_DIR / "articles_frozen.json").read_text(encoding="utf-8"))
    rows = build_article_template(frozen["articles"], **kwargs)
    return write_csv(out_path or LABELS_DIR / "agent1b_articles_template.csv", ARTICLE_COLUMNS, rows)


def load_firestore_papers(project_id, n_runs=8):
    """Read-only: paper summaries from the most recent pipeline runs (needs ADC credentials)."""
    from google.cloud import firestore
    db = firestore.Client(project=project_id)
    query = db.collection("pipeline_runs").order_by("started_at", direction=firestore.Query.DESCENDING).limit(n_runs)
    papers = []
    for doc in query.stream():
        summaries = doc.to_dict().get("paper_summaries") or {}
        papers.extend(summaries.get("papers", []) if isinstance(summaries, dict) else summaries)
    return papers


def make_summary_template(data_dir="data", out_path=None, project_id=None, **kwargs):
    """News and local papers come from `data/`; extra papers from Firestore when a project id is given."""
    import json
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
    from agent2a_summarize_papers import download_and_extract
    from article_fetch import fetch_article_text

    data_dir = Path(data_dir)
    news = json.loads((data_dir / "news_summaries.json").read_text(encoding="utf-8"))["articles"]
    papers = json.loads((data_dir / "paper_summaries.json").read_text(encoding="utf-8"))["papers"]
    if project_id:
        papers = {p["id"]: p for p in papers + load_firestore_papers(project_id)}.values()
    rows, skipped = build_summary_template(list(news), list(papers), fetch_article_text, download_and_extract, **kwargs)
    print(f"{len(rows)} rows; {skipped} candidates skipped because their source text could not be fetched")
    return write_csv(out_path or LABELS_DIR / "summaries_template.csv", SUMMARY_COLUMNS, rows)


if __name__ == "__main__":
    import os
    which = sys.argv[1] if len(sys.argv) > 1 else "articles"
    if which == "articles":
        print(f"wrote {make_article_template()}")
    elif which == "summaries":
        print(f"wrote {make_summary_template(project_id=os.environ.get('GCP_PROJECT_ID') or None)}")
    else:
        sys.exit(f"unknown template {which!r}")
