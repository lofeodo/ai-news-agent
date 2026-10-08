"""Build the frozen dedup eval cases and a blank labeling template.

A case is one section plus ~8 articles. Suspected duplicate pairs are proposed by word overlap
(stdlib only); the owner decides what is really a duplicate. `duplicate_group` is always written
empty: gold labels are written by hand, never by a script.

Labeling rule (same as prompts/dedup_prompt.txt): same event = duplicate, even across outlets,
languages and angles; same company or topic alone is not. Put the same label (g1, g2, ...) on
articles that are duplicates of each other; blank = unique; "?" in `note` = ambiguous, excluded.

CMD:  python -m evals.make_dedup_cases   (reads data/news_summaries.json and, if present,
      evals/fixtures/dedup_synthetic.json; writes the fixture and evals/labels/dedup_cases_template.csv)
"""
import json
import random
import re
from itertools import combinations
from pathlib import Path

from evals.make_label_templates import LABELS_DIR, write_csv
from evals.snapshot import FIXTURES_DIR, article_id, snippet

CASES_PATH = FIXTURES_DIR / "dedup_cases.json"
SYNTHETIC_PATH = FIXTURES_DIR / "dedup_synthetic.json"
TEMPLATE_PATH = LABELS_DIR / "dedup_cases_template.csv"
COLUMNS = ["case_id", "article_id", "title", "snippet", "kind", "duplicate_group", "note"]

CASE_SIZE = 8
DUP_OVERLAP = 0.5       # at or above: suspected duplicate
NEG_OVERLAP = 0.25      # between this and DUP_OVERLAP: same topic, probably a hard negative
FILLER_MAX_OVERLAP = 0.2
_STOP = set("""about after again also another because been before being between both could does from have
into just more most much only other over said says should some still than that their them then there these
they this those through under very what when where which while will with without would your""".split())


def tokens(article):
    text = f"{article.get('title', '')} {article.get('description') or article.get('summary') or ''}"
    return {w for w in re.findall(r"[a-zà-ÿ0-9]{4,}", text.lower()) if w not in _STOP}


def overlap(a, b):
    """Shared words over the smaller word set (0..1)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _entry(a):
    return {
        "id": article_id(a["url"]), "url": a["url"], "title": a.get("title", ""),
        "snippet": snippet(a.get("description")), "summary": a.get("summary") or "",
        "hn_score": a.get("hn_score"), "language": a.get("language"), "source": a.get("source"),
    }


def _make_case(case_id, kind, category, members, fillers, rng):
    rows = [_entry(a) for a in members + fillers]
    rng.shuffle(rows)
    return {"case_id": case_id, "kind": kind, "category": category, "articles": rows}


def build_dedup_cases(articles, n_real=20, n_hard=10, n_control=5, seed=0, size=CASE_SIZE):
    """Seeded cases from a pool of summarized articles. Never touches labels."""
    rng = random.Random(seed)
    pool = sorted((a for a in articles if a.get("url") and a.get("category")), key=lambda a: a["url"])
    by_cat = {}
    for a in pool:
        by_cat.setdefault(a["category"], []).append(a)
    toks = {a["url"]: tokens(a) for a in pool}

    pairs = []   # (score, category, a, b)
    for cat, items in sorted(by_cat.items()):
        for a, b in combinations(items, 2):
            s = overlap(toks[a["url"]], toks[b["url"]])
            if s >= NEG_OVERLAP:
                pairs.append((s, cat, a, b))
    pairs.sort(key=lambda p: (-p[0], p[2]["url"], p[3]["url"]))

    used, cases = set(), []

    def take(kind, want, selector, prefix):
        made = 0
        for s, cat, a, b in pairs:
            if made == want:
                break
            if not selector(s) or a["url"] in used or b["url"] in used:
                continue
            members = [a, b]
            ok = [x for x in by_cat[cat] if x["url"] not in used and x not in members
                  and all(overlap(toks[x["url"]], toks[m["url"]]) < FILLER_MAX_OVERLAP for m in members)]
            if len(ok) < size - 2:
                continue
            fillers = []
            for x in rng.sample(ok, len(ok)):
                if all(overlap(toks[x["url"]], toks[f["url"]]) < FILLER_MAX_OVERLAP for f in fillers):
                    fillers.append(x)
                if len(fillers) == size - 2:
                    break
            if len(fillers) < size - 2:
                continue
            used.update(m["url"] for m in members + fillers)
            made += 1
            cases.append(_make_case(f"{prefix}{made:02d}", kind, cat, members, fillers, rng))

    take("real", n_real, lambda s: s >= DUP_OVERLAP, "real")
    take("hard_negative", n_hard, lambda s: NEG_OVERLAP <= s < DUP_OVERLAP, "hard")

    made = 0
    for cat, items in sorted(by_cat.items()):
        free = [x for x in items if x["url"] not in used]
        while made < n_control and len(free) >= size:
            picked = []
            for x in rng.sample(free, len(free)):
                if all(overlap(toks[x["url"]], toks[p["url"]]) < FILLER_MAX_OVERLAP for p in picked):
                    picked.append(x)
                if len(picked) == size:
                    break
            if len(picked) < size:
                break
            used.update(p["url"] for p in picked)
            free = [x for x in free if x["url"] not in used]
            made += 1
            cases.append(_make_case(f"control{made:02d}", "control", cat, picked, [], rng))
    return cases


def build_synthetic_cases(synthetic, articles, seed=0, size=CASE_SIZE):
    """Cases from hand-written rewrites.

    Each synthetic item: {"source_url", "title", "summary", "language"?, "angle"?}. The rewrite is added
    next to its real source article, plus same-category fillers. Items whose source is missing are skipped.
    """
    rng = random.Random(seed)
    by_url = {a["url"]: a for a in articles}
    used, cases = set(), []
    for n, item in enumerate(sorted(synthetic, key=lambda s: s["source_url"]), start=1):
        src = by_url.get(item["source_url"])
        if not src:
            continue
        fake = {"url": f"synthetic:{n:02d}:{item['source_url']}", "title": item["title"],
                "description": item["summary"], "summary": item["summary"], "category": src["category"],
                "language": item.get("language", src.get("language")), "source": "synthetic",
                "hn_score": None}
        src_toks = tokens(src)
        ok = [x for x in sorted(articles, key=lambda a: a["url"])
              if x.get("category") == src["category"] and x["url"] != src["url"] and x["url"] not in used
              and overlap(tokens(x), src_toks) < FILLER_MAX_OVERLAP]
        fillers = rng.sample(ok, min(size - 2, len(ok)))
        used.update(f["url"] for f in fillers)
        cases.append(_make_case(f"synth{n:02d}", "synthetic", src["category"], [src, fake], fillers, rng))
    return cases


def template_rows(cases):
    return [{"case_id": c["case_id"], "article_id": a["id"], "title": a["title"], "snippet": a["snippet"],
             "kind": c["kind"], "duplicate_group": "", "note": ""}
            for c in cases for a in c["articles"]]


def write_outputs(cases, cases_path=CASES_PATH, template_path=TEMPLATE_PATH):
    cases_path = Path(cases_path)
    cases_path.parent.mkdir(parents=True, exist_ok=True)
    cases_path.write_text(json.dumps({"cases": cases}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_csv(template_path, COLUMNS, template_rows(cases))
    return cases_path, Path(template_path)


def main(data_dir="data", **kwargs):
    articles = json.loads((Path(data_dir) / "news_summaries.json").read_text(encoding="utf-8"))["articles"]
    cases = build_dedup_cases(articles, **kwargs)
    if SYNTHETIC_PATH.exists():
        synthetic = json.loads(SYNTHETIC_PATH.read_text(encoding="utf-8"))
        cases += build_synthetic_cases(synthetic, articles, seed=kwargs.get("seed", 0))
    paths = write_outputs(cases)
    from collections import Counter
    print(dict(Counter(c["kind"] for c in cases)), "->", *paths)


if __name__ == "__main__":
    main()
