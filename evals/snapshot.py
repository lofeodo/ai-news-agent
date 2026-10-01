"""Snapshot a real run into frozen eval inputs (public repo: no third-party full text is tracked).

Tracked output (`evals/fixtures/`): ids, urls, titles, short snippets, categories.
Full text re-fetched by script goes under `evals/fixtures/private/` (gitignored).

CMD:  python -m evals.snapshot   (reads data/, writes evals/fixtures/articles_frozen.json)
"""
import hashlib
import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PRIVATE_DIR = FIXTURES_DIR / "private"
SNIPPET_CHARS = 300


def article_id(url):
    """Stable short id derived from the url."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def snippet(text, limit=SNIPPET_CHARS):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def join_articles(news_filtered, review_log):
    """Join agent1b's selected articles with their audit rows (by url).

    Articles without an audit row (e.g. a single_pass run) get confidence/first_pass None.
    """
    audit = {row["url"]: row for row in review_log.get("articles", [])}
    joined = []
    for art in news_filtered.get("articles", []):
        row = audit.get(art["url"], {})
        joined.append({
            "id": article_id(art["url"]),
            "url": art["url"],
            "title": art.get("title", ""),
            "snippet": snippet(art.get("description")),
            "source": art.get("source"),
            "language": art.get("language"),
            "hn_score": art.get("hn_score"),
            "final_category": art.get("category"),
            "first_pass_category": row.get("first_pass_category"),
            "confidence": row.get("confidence"),
            "review_status": row.get("review_status"),
        })
    return joined


def freeze_articles(data_dir="data", out_path=None):
    data_dir = Path(data_dir)
    news = json.loads((data_dir / "news_filtered.json").read_text(encoding="utf-8"))
    review = json.loads((data_dir / "agent1b_review_log.json").read_text(encoding="utf-8"))
    frozen = {
        "source_run_at": news.get("run_at"),
        "review_run_id": review.get("run_id"),
        "articles": join_articles(news, review),
    }
    out_path = Path(out_path or FIXTURES_DIR / "articles_frozen.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(frozen, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


def save_private_text(subdir, item_id, text, private_dir=PRIVATE_DIR):
    """Write full text to the gitignored private dir; returns the path relative to the evals dir."""
    path = Path(private_dir) / subdir / f"{item_id}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


if __name__ == "__main__":
    print(f"wrote {freeze_articles()}")
