"""On-demand review-step eval: agent1b single_pass vs graph on frozen, gold-labelled articles.

Makes REAL Claude calls (and live article fetches for the review loop). Never collected by pytest.

CMD:
    venv\\Scripts\\python -m evals.run_review_eval --dry-run     (counts + cost estimate, no API calls)
    venv\\Scripts\\python -m evals.run_review_eval               (refuses if the estimate exceeds 2 USD)
    venv\\Scripts\\python -m evals.run_review_eval --approve     (only after the owner OKs the estimate)

Both arms categorize the same 500 frozen articles in production-sized batches (so batch context matches
production). Only the gold-labelled ones are scored, and only those below the confidence threshold go
through the review loop, with the production per-run cap lifted. Categorization is re-run fresh, so
accuracy is not circular with the first-pass column the labeler saw.
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))
os.chdir(ROOT)  # agents open prompts/... relative to the repo root

import agent1b_fetch_news as a1b  # noqa: E402
import config  # noqa: E402
from agent1b_graph import MeteredClient, ReviewConfig, _Meter, build_review_graph  # noqa: E402
from evals import cost, results, review_eval  # noqa: E402
from evals.snapshot import FIXTURES_DIR, PRIVATE_DIR  # noqa: E402
from evals.stats import rate_with_ci  # noqa: E402

FROZEN_PATH = FIXTURES_DIR / "articles_frozen.json"
GOLD_PATH = ROOT / "evals" / "labels" / "agent1b_articles_template.csv"
FETCH_CACHE_PATH = PRIVATE_DIR / "review_fetch_cache.json"

# Rough per-call token budgets used only for the pre-run estimate (actual usage is metered).
_PROMPT_OVERHEAD_TOKENS = 1500   # filter prompt + tool schema per batch
_OUT_TOKENS_PER_ARTICLE = 30
_REVIEW_IN_TOKENS = 6000         # two calls plus up to ~2k tokens of fetched text
_REVIEW_OUT_TOKENS = 300


def load_frozen(path=FROZEN_PATH):
    """Frozen articles in the shape the agent1b helpers expect (snippet -> description)."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return [{"source": a.get("source", ""), "title": a["title"], "description": a.get("snippet", ""),
             "url": a["url"], "language": a.get("language", "en"), "hn_score": a.get("hn_score"),
             "_id": a["id"]} for a in doc["articles"]]


def estimate_tokens(articles, review_articles):
    """(input_tokens, output_tokens) for both categorize arms plus the worst-case review loop."""
    chars = sum(len(a["title"]) + len(a["description"]) + 40 for a in articles)
    batches = -(-len(articles) // a1b.FILTER_BATCH_SIZE)
    arm_in = chars // 4 + batches * _PROMPT_OVERHEAD_TOKENS
    arm_out = len(articles) * _OUT_TOKENS_PER_ARTICLE
    return (2 * arm_in + review_articles * _REVIEW_IN_TOKENS,
            2 * arm_out + review_articles * _REVIEW_OUT_TOKENS)


def reconcile_gold(gold, articles):
    """Re-key gold rows whose id doesn't match the frozen set (e.g. Excel turned an id into 5.19E+11) by url.

    The labels file is the owner's, so it is never edited; the mismatch is repaired in memory here.
    """
    ids = {a["_id"] for a in articles}
    by_url = {a["url"]: a["_id"] for a in articles}
    fixed = {}
    for gid, g in gold.items():
        fixed[gid if gid in ids else by_url.get(g.get("url"), gid)] = g
    return fixed


class CachedFetcher:
    """Wraps the article fetcher with an on-disk cache (gitignored) so reruns are reproducible."""

    def __init__(self, fetcher, path=FETCH_CACHE_PATH):
        self._fetcher, self._path, self._lock = fetcher, Path(path), threading.Lock()
        try:
            self._cache = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._cache = {}

    def __call__(self, url, timeout):
        with self._lock:
            hit = self._cache.get(url)
        if hit is not None:
            return tuple(hit)
        text, reason = self._fetcher(url, timeout)
        with self._lock:
            self._cache[url] = [text, reason]
        return text, reason

    def save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._cache), encoding="utf-8")


def _categorize(articles, client, with_confidence):
    meter = _Meter()
    start = time.monotonic()
    out = a1b.filter_and_categorize(articles, client=MeteredClient(client, meter), with_confidence=with_confidence)
    return {a["url"]: a for a in out}, meter.as_usage(), time.monotonic() - start


def _review(articles, client, fetcher, cfg):
    """Run the production review subgraph on each article (parallel, bounded like production)."""
    graph = build_review_graph(client, fetcher, cfg)

    def one(a):
        state = graph.invoke({"article": a, "messages": [], "iterations": 0})
        return state["reviewed"][0]

    with ThreadPoolExecutor(max_workers=a1b.MAX_CONCURRENT_CLAUDE_CALLS) as pool:
        return {r["url"]: r for r in pool.map(one, articles)}


def run_eval(articles, gold, client, fetcher, cfg):
    """Returns (rows, usage). rows: gold articles scored by BOTH arms (so the paired comparison is fair)."""
    labeled = {a["_id"] for a in articles} & set(gold)
    single, sp_usage, sp_secs = _categorize(articles, client, with_confidence=False)
    first, gr_usage, gr_secs = _categorize(articles, client, with_confidence=True)

    to_review = [a for u, a in first.items()
                 if a["_id"] in labeled and (a.get("confidence") is None or a["confidence"] < cfg.confidence_threshold)]
    t0 = time.monotonic()
    reviewed = _review(to_review, client, fetcher, cfg) if to_review else {}
    review_secs = time.monotonic() - t0

    predictions = {}
    for url, a in first.items():
        if a["_id"] not in labeled or url not in single:
            continue
        r = reviewed.get(url)
        predictions[a["_id"]] = {
            "single_pass": single[url]["category"], "first_pass": a["category"],
            "confidence": a.get("confidence"), "final": r["final_category"] if r else a["category"],
            "routed": r is not None, "tool_calls": r["tool_calls"] if r else 0,
            "input_tokens": r["input_tokens"] if r else 0, "output_tokens": r["output_tokens"] if r else 0,
            "review_status": r["status"] if r else "skipped",
        }
    review_in = sum(r["input_tokens"] for r in reviewed.values())
    review_out = sum(r["output_tokens"] for r in reviewed.values())
    usage = {
        "single_pass": {**sp_usage, "seconds": sp_secs},
        "graph_categorize": {**gr_usage, "seconds": gr_secs},
        "review": {"input": review_in, "output": review_out, "seconds": review_secs, "articles": len(reviewed)},
        "gold_total": len(gold), "gold_in_frozen": len(labeled),
    }
    return review_eval.join(gold, predictions), usage


def usage_metrics(rows, usage):
    """Coverage, token and latency metrics (no interval: they are measurements, not rates)."""
    def m(value, n):
        return {"value": value, "n": n, "ci_low": None, "ci_high": None}
    out = {"coverage_scored_by_both_arms": rate_with_ci(len(rows), usage["gold_total"])}
    for arm, u in (("single_pass", usage["single_pass"]), ("graph_categorize", usage["graph_categorize"]),
                   ("review", usage["review"])):
        n = u.get("articles", usage["gold_in_frozen"])
        out[f"tokens_input__{arm}"] = m(u.get("input", 0), n)
        out[f"tokens_output__{arm}"] = m(u.get("output", 0), n)
        out[f"seconds__{arm}"] = m(round(u["seconds"], 2), n)
    return out


def total_cost(usage):
    tin = sum(usage[k].get("input", 0) for k in ("single_pass", "graph_categorize", "review"))
    tout = sum(usage[k].get("output", 0) for k in ("single_pass", "graph_categorize", "review"))
    return cost.estimate_cost(tin, tout)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print counts and the cost estimate, call nothing")
    ap.add_argument("--approve", action="store_true", help="owner approved an estimate above the 2 USD limit")
    ap.add_argument("--name", default=f"review_eval_{date.today().isoformat()}")
    args = ap.parse_args(argv)

    articles = load_frozen()
    gold = reconcile_gold(review_eval.load_gold(GOLD_PATH), articles)
    cfg = ReviewConfig.from_env()
    in_frozen = [a for a in articles if a["_id"] in gold]
    est_in, est_out = estimate_tokens(articles, review_articles=len(in_frozen))
    est = cost.estimate_cost(est_in, est_out)
    print(f"frozen articles: {len(articles)}; gold labels: {len(gold)}; gold present in frozen set: {len(in_frozen)}")
    print(f"review threshold: confidence < {cfg.confidence_threshold} (worst case all {len(in_frozen)} reviewed)")
    print(f"estimated worst-case cost: ${est:.3f} ({est_in:,} in / {est_out:,} out tokens, model {config.SCORING_MODEL})")
    if args.dry_run:
        return 0
    cost.CostGuard(approved=args.approve).check(est)

    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_1ST_API_KEY"))
    from article_fetch import fetch_article_text_result
    fetcher = CachedFetcher(fetch_article_text_result)
    # Lift the production per-run cap: the eval wants every low-confidence labeled article reviewed.
    eval_cfg = ReviewConfig(cfg.confidence_threshold, max(cfg.max_articles, len(in_frozen)),
                            cfg.max_iterations, cfg.fetch_timeout)
    try:
        rows, usage = run_eval(articles, gold, client, fetcher, eval_cfg)
    finally:
        fetcher.save()

    metrics, verdict = review_eval.score(rows, cfg.confidence_threshold)
    metrics.update(usage_metrics(rows, usage))
    actual = total_cost(usage)
    notes = (f"{verdict} Review threshold: confidence < {cfg.confidence_threshold}; production cap "
             f"({cfg.max_articles}) lifted. Frozen snippets are truncated to 300 chars, so both arms see less "
             "text than production. Gold labels may be anchored by the first-pass category shown to the labeler.")
    doc = results.build_results(args.name, config.SCORING_MODEL, metrics, cost_usd=round(actual, 4), notes=notes)
    path = results.write_results(doc)
    rows_path = path.with_name(f"{args.name}_rows.json")
    rows_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path} and {rows_path.name}; cost ${actual:.3f}")
    print(verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
