"""Generate blank labeling templates from frozen eval inputs.

The label columns (`gold_category`, `supported`) are always written empty: gold labels are
written by hand by the repo owner, never by a script.

CMD:  python -m evals.make_label_templates articles
"""
import csv
import random
import sys
from pathlib import Path

from evals.snapshot import FIXTURES_DIR

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


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "articles"
    if which == "articles":
        print(f"wrote {make_article_template()}")
    else:
        sys.exit(f"unknown template {which!r}")
