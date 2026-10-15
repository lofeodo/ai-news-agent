"""Freeze the "before" for cost-roadmap Step 3: agent2b summaries and agent1a paper scoring.

Runs the UNMODIFIED agent functions (agent2b.summarize_article, agent1a.score_paper) on a fixed input set
and records what Haiku produces today, so a later change (batching, abstract-first scoring) can be
compared on identical inputs. Makes REAL Claude calls; never collected by pytest.

CMD:
    venv\\Scripts\\python -m evals.freeze_baseline summaries --dry-run
    venv\\Scripts\\python -m evals.freeze_baseline summaries [--run RUN_ID] [--n-other 30]
    venv\\Scripts\\python -m evals.freeze_baseline papers --dry-run
    venv\\Scripts\\python -m evals.freeze_baseline papers [--repeats 2]

Public repo: tracked output holds ids, urls, titles, hashes, token counts and the model's own short outputs.
Third-party full text (article bodies, PDFs) goes under `evals/fixtures/private/` (gitignored).
Paper scoring is run `--repeats` times (default 2) because Haiku is not deterministic: the agreement
between two runs of the *same* code is the noise floor any later change has to be judged against.
"""
import argparse
import hashlib
import json
import os
import random
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))
os.chdir(ROOT)  # agents open prompts/... relative to the repo root
os.environ["LANGSMITH_TRACING"] = "false"  # a baseline run must not land in the production trace project

from evals import cost, results  # noqa: E402
from evals.snapshot import FIXTURES_DIR, PRIVATE_DIR  # noqa: E402
from evals.stats import rate_with_ci  # noqa: E402

PROJECT = "ai-news-letter-497720"
DEFAULT_RUN = "2026-10-11T160004Z"  # latest release run when this step started
SUMMARIES_PATH = FIXTURES_DIR / "summaries_frozen.json"
PAPERS_PATH = FIXTURES_DIR / "papers_frozen.json"
SUMMARY_SOURCES_PRIVATE = PRIVATE_DIR / "summary_sources_frozen.json"
PAPER_TEXT_PRIVATE = PRIVATE_DIR / "papers_text_frozen.json"

CLAUDE_DIMENSIONS = ("novelty", "rigor", "reproducibility", "clarity", "practical_applicability",
                     "significance", "disruption_potential", "wow_factor")

# Rough per-call budgets, used only for the pre-run estimate (actual usage is metered).
_NEWS_PROMPT_TOKENS = 450
_NEWS_OUT_TOKENS = 70
_PAPER_PROMPT_TOKENS = 2500   # rubric + tool schema + abstract
_PAPER_TEXT_TOKENS = 7000     # WORD_CUTOFF words of PDF text
_PAPER_OUT_TOKENS = 250

_FR_WORDS = frozenset("le la les des une un du de et est en que qui pour dans sur avec par au aux ce cette "
                      "son sa ses nous vous ils elle plus pas sont été à où ou mais comme".split())


# ---------------------------------------------------------------------------
# Pure helpers (unit tested; no network)
# ---------------------------------------------------------------------------

def sha1(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12]


def infer_language(text: str) -> str:
    """'fr' when French function words dominate the first 300 words, else 'en'.

    Only used for non-shipped articles, whose language was not stored; shipped ones carry the real value.
    """
    words = re.findall(r"[a-zà-ÿ']+", (text or "").lower())[:300]
    if len(words) < 20:
        return "en"
    return "fr" if sum(w in _FR_WORDS for w in words) / len(words) > 0.18 else "en"


def pick_sample(shipped_urls: list, other_urls: list, n_other: int, seed: int = 0) -> list:
    """All shipped articles, plus a seeded random sample of the rest. Order is stable."""
    rng = random.Random(seed)
    others = sorted(other_urls)
    picked = rng.sample(others, min(n_other, len(others)))
    return list(shipped_urls) + sorted(picked)


def word_count(text) -> int:
    return len((text or "").split())


def spearman(a: list, b: list):
    """Rank correlation of two equal-length score lists, or None when it is undefined."""
    if len(a) != len(b) or len(a) < 3 or len(set(a)) < 2 or len(set(b)) < 2:
        return None
    from scipy.stats import spearmanr
    return float(spearmanr(a, b).statistic)


def top_k_overlap(ref: dict, new: dict, k: int) -> float | None:
    """Share of the reference top-k ids that are also in the new top-k. Inputs: {id: score}."""
    ids = sorted(set(ref) & set(new))
    if len(ids) < k or k < 1:
        return None
    top = lambda d: set(sorted(ids, key=lambda i: (-d[i], i))[:k])  # noqa: E731
    return len(top(ref) & top(new)) / k


def summarize_outputs(items: list) -> dict:
    """Aggregate the metrics a later change is compared on."""
    ok = [i for i in items if i.get("summary")]
    words = [word_count(i["summary"]) for i in ok]
    return {
        "n": len(items),
        "n_summarized": len(ok),
        "n_fallback": sum(1 for i in items if i.get("used_fallback")),
        "mean_words": sum(words) / len(words) if words else None,
        "input_tokens": sum(i.get("input_tokens", 0) for i in items),
        "output_tokens": sum(i.get("output_tokens", 0) for i in items),
    }


# ---------------------------------------------------------------------------
# Token metering
# ---------------------------------------------------------------------------

class _Meter:
    def __init__(self):
        self._lock = threading.Lock()
        self.input = self.output = self.calls = 0

    def record(self, response):
        usage = getattr(response, "usage", None)
        with self._lock:
            self.calls += 1
            self.input += getattr(usage, "input_tokens", 0) or 0
            self.output += getattr(usage, "output_tokens", 0) or 0


class _MeteredMessages:
    def __init__(self, inner, meter):
        self._inner, self._meter = inner, meter

    def create(self, **kwargs):
        response = self._inner.create(**kwargs)
        self._meter.record(response)
        return response


class _MeteredClient:
    def __init__(self, client, meter):
        self.messages = _MeteredMessages(client.messages, meter)


def _real_client(**kw):
    import anthropic
    key = os.environ.get("ANTHROPIC_1ST_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_1ST_API_KEY is not set")
    return anthropic.Anthropic(api_key=key, **kw)


def _model_cost(model, tokens_in, tokens_out):
    return cost.estimate_cost(tokens_in, tokens_out, model)


# ---------------------------------------------------------------------------
# agent2b / news summaries
# ---------------------------------------------------------------------------

def load_summary_inputs(run_id: str) -> dict:
    """Read the run's shipped articles, audit categories and persisted source text from Firestore (read-only)."""
    from google.cloud import firestore
    db = firestore.Client(project=PROJECT)
    run = db.collection("pipeline_runs").document(run_id).get().to_dict() or {}
    shipped = {}
    for category, arts in (run.get("news_summaries") or {}).items():
        for a in arts:
            if a.get("url") and a.get("summary"):
                shipped[a["url"]] = {**a, "category": a.get("category") or category}
    audit = db.collection("agent1b_audits").document(run_id).get().to_dict() or {}
    cats = {r["url"]: r.get("final_category") for r in audit.get("articles", []) if r.get("url")}
    sources = {}
    for snap in db.collection("summary_sources").where(filter=firestore.FieldFilter("run_id", "==", run_id)).stream():
        d = snap.to_dict()
        if d.get("kind") == "news" and d.get("text"):
            sources[d["ident"]] = {"text": d["text"], "title": d.get("title", ""),
                                   "used_fallback": bool(d.get("used_fallback"))}
    return {"shipped": shipped, "categories": cats, "sources": sources}


def build_articles(inputs: dict, urls: list) -> list:
    """Article dicts in the shape agent2b.summarize_article takes, plus the text to feed it."""
    out = []
    for url in urls:
        src = inputs["sources"][url]
        ship = inputs["shipped"].get(url)
        fallback = src["used_fallback"]
        lang = ship.get("language") if ship else infer_language(src["text"])
        article = {
            "title": (ship or {}).get("title") or src["title"],
            "url": url,
            "language": lang,
            "description": src["text"] if fallback else ((ship or {}).get("description") or ""),
            "category": (ship or {}).get("category") or inputs["categories"].get(url),
            "hn_score": (ship or {}).get("hn_score"),
        }
        out.append({"article": article, "text": None if fallback else src["text"],
                    "shipped": ship is not None, "language_inferred": ship is None,
                    "production_summary": (ship or {}).get("summary"), "source_text": src["text"]})
    return out


def estimate_summaries(rows: list) -> tuple[int, int]:
    tokens_in = sum(_NEWS_PROMPT_TOKENS + min(len(r["source_text"]) // 4, 3500) for r in rows)
    return tokens_in, len(rows) * _NEWS_OUT_TOKENS


def cmd_summaries(args) -> int:
    import config
    import agent2b_summarize_news as a2b

    inputs = load_summary_inputs(args.run)
    shipped = [u for u in inputs["shipped"] if u in inputs["sources"]]
    others = [u for u in inputs["sources"] if u not in inputs["shipped"] and inputs["categories"].get(u)]
    urls = pick_sample(shipped, others, args.n_other, seed=args.seed)
    rows = build_articles(inputs, urls)
    est_in, est_out = estimate_summaries(rows)
    est = _model_cost(config.SCORING_MODEL, est_in, est_out)
    print(f"run {args.run}: {len(shipped)} shipped with source, {len(others)} other; sampling {len(rows)} "
          f"({sum(r['language_inferred'] for r in rows)} with inferred language). "
          f"Estimated cost ${est:.3f}")
    if args.dry_run:
        return 0
    cost.CostGuard(approved=args.approve).check(est)

    templates = {name: Path("prompts", f"{name}.txt").read_text(encoding="utf-8")
                 for name in ("news_summary_prompt", "news_summary_fallback_prompt", "quebec_french_style")}
    real = _real_client(timeout=60.0)

    def one(row):
        meter = _Meter()
        res = a2b.summarize_article(row["article"], row["text"], templates["news_summary_prompt"],
                                    templates["news_summary_fallback_prompt"], templates["quebec_french_style"],
                                    _MeteredClient(real, meter))
        return {"id": sha1(row["article"]["url"]), "url": row["article"]["url"], "title": row["article"]["title"],
                "category": row["article"]["category"], "language": row["article"]["language"],
                "language_inferred": row["language_inferred"], "hn_score": row["article"]["hn_score"],
                "shipped": row["shipped"], "used_fallback": res.get("used_fallback"),
                "source_chars": len(row["source_text"]), "source_sha1": sha1(row["source_text"]),
                "summary": res.get("summary"), "summary_error": res.get("summary_error"),
                "production_summary": row["production_summary"],
                "input_tokens": meter.input, "output_tokens": meter.output}

    with ThreadPoolExecutor(max_workers=3) as ex:
        items = list(ex.map(one, rows))

    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_SOURCES_PRIVATE.write_text(json.dumps(
        {r["article"]["url"]: {"text": r["source_text"], "used_fallback": r["text"] is None} for r in rows},
        ensure_ascii=False), encoding="utf-8")
    agg = summarize_outputs(items)
    actual = _model_cost(config.SCORING_MODEL, agg["input_tokens"], agg["output_tokens"])
    doc = {"frozen_at": date.today().isoformat(), "source_run_id": args.run, "model": config.SCORING_MODEL,
           "seed": args.seed, "prompt_sha1": {k: sha1(v) for k, v in templates.items()},
           "note": "Reference = unmodified agent2b.summarize_article on the persisted source text. Source text "
                   "is third-party and lives in evals/fixtures/private/ (gitignored).",
           "totals": {**agg, "cost_usd": round(actual, 4)}, "items": items}
    SUMMARIES_PATH.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {SUMMARIES_PATH.relative_to(ROOT)}: {agg['n_summarized']}/{agg['n']} summarized, "
          f"{agg['n_fallback']} fallback, mean {agg['mean_words']:.1f} words, actual cost ${actual:.3f}")
    return 0


# ---------------------------------------------------------------------------
# agent1a / paper scoring
# ---------------------------------------------------------------------------

def estimate_papers(n_papers: int, repeats: int) -> tuple[int, int]:
    per_in = _PAPER_PROMPT_TOKENS + _PAPER_TEXT_TOKENS
    return n_papers * repeats * per_in, n_papers * repeats * _PAPER_OUT_TOKENS


def cmd_papers(args) -> int:
    import config
    import agent1a_fetch_papers as a1a

    papers = a1a.select_trending_papers("freeze-baseline", seen=set())
    est_in, est_out = estimate_papers(len(papers), args.repeats)
    est = _model_cost(config.SCORING_MODEL, est_in, est_out)
    print(f"{len(papers)} trending papers; {args.repeats} scoring passes. Estimated cost ${est:.3f}")
    if args.dry_run:
        return 0
    cost.CostGuard(approved=args.approve).check(est)

    texts, used_abstract = {}, {}
    for p in papers:
        pid = p["id"].split("/")[-1]
        try:
            texts[p["id"]] = a1a.download_and_extract(p["pdf_url"], pid)
            used_abstract[p["id"]] = False
        except Exception as e:
            print(f"  [pdf] {pid}: {e}; scoring from the abstract", flush=True)
            texts[p["id"]] = p["abstract"]
            used_abstract[p["id"]] = True

    real = _real_client(timeout=60.0)
    meter = _Meter()
    a1a.tracing.make_client = lambda *a, **k: _MeteredClient(real, meter)  # score_paper builds its own client

    runs = []
    for r in range(args.repeats):
        scored = {}
        for p in papers:
            before_in, before_out = meter.input, meter.output
            try:
                s = a1a.score_paper(p, texts[p["id"]])
                err = None
            except Exception as e:
                s, err = None, str(e)
            scored[p["id"]] = {"scores": s, "error": err, "input_tokens": meter.input - before_in,
                               "output_tokens": meter.output - before_out}
        runs.append(scored)
        print(f"  pass {r + 1}/{args.repeats} done", flush=True)

    items = []
    for p in papers:
        pid = p["id"]
        passes = [run[pid] for run in runs]
        items.append({
            "id": pid, "title": p["title"], "upvotes": p.get("upvotes"),
            "community_traction": p.get("community_traction"), "pdf_url": p.get("pdf_url"),
            "text_chars": len(texts[pid]), "text_sha1": sha1(texts[pid]), "used_abstract": used_abstract[pid],
            "passes": [{"scores": x["scores"], "error": x["error"],
                        "input_tokens": x["input_tokens"], "output_tokens": x["output_tokens"]} for x in passes],
        })

    def totals(pass_index):
        return {i["id"]: i["passes"][pass_index]["scores"]["claude_total"]
                for i in items if i["passes"][pass_index]["scores"]}

    noise = None
    if args.repeats >= 2:
        a, b = totals(0), totals(1)
        ids = sorted(set(a) & set(b))
        noise = {"spearman_pass1_vs_pass2": spearman([a[i] for i in ids], [b[i] for i in ids]),
                 "top8_overlap_pass1_vs_pass2": top_k_overlap(a, b, 8), "n": len(ids)}
    tokens_in = sum(p["input_tokens"] for i in items for p in i["passes"])
    tokens_out = sum(p["output_tokens"] for i in items for p in i["passes"])
    actual = _model_cost(config.SCORING_MODEL, tokens_in, tokens_out)

    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_TEXT_PRIVATE.write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")
    doc = {"frozen_at": date.today().isoformat(), "model": config.SCORING_MODEL, "repeats": args.repeats,
           "word_cutoff": config.WORD_CUTOFF, "dimensions": list(CLAUDE_DIMENSIONS),
           "rubric_sha1": sha1(Path("prompts/scoring_rubric.txt").read_text(encoding="utf-8")),
           "note": "Reference = pass 1 of the unmodified agent1a.score_paper; pass 2 measures Haiku's own "
                   "run-to-run noise, the floor any change is judged against. Paper text is in "
                   "evals/fixtures/private/ (gitignored); the abstract-first change needs it.",
           "noise_floor": noise,
           "totals": {"input_tokens": tokens_in, "output_tokens": tokens_out, "cost_usd": round(actual, 4)},
           "items": items}
    PAPERS_PATH.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {PAPERS_PATH.relative_to(ROOT)}: noise floor {noise}; actual cost ${actual:.3f}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("summaries", help="freeze agent2b news summaries")
    s.add_argument("--run", default=DEFAULT_RUN)
    s.add_argument("--n-other", type=int, default=30, help="non-shipped articles to add to the shipped ones")
    s.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("papers", help="freeze agent1a paper scoring")
    p.add_argument("--repeats", type=int, default=2)
    for x in (s, p):
        x.add_argument("--dry-run", action="store_true")
        x.add_argument("--approve", action="store_true", help="only after the owner OKs an estimate over 2 USD")
    args = ap.parse_args(argv)
    return cmd_summaries(args) if args.cmd == "summaries" else cmd_papers(args)


if __name__ == "__main__":
    sys.exit(main())
