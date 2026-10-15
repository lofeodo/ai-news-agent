"""On-demand eval of agent2b's shortlist mode: do the editor's picks survive summarizing less?

Makes REAL Claude calls and live article fetches. Never collected by pytest.

CMD:
    venv\\Scripts\\python -m evals.run_shortlist_eval --dry-run
    venv\\Scripts\\python -m evals.run_shortlist_eval            (refuses if the estimate exceeds 2 USD)
    venv\\Scripts\\python -m evals.run_shortlist_eval --approve   (only after the owner OKs the estimate)

Pool: the 500 frozen articles in `evals/fixtures/articles_frozen.json` (title, HN points, language,
category, 300-char snippet). Page text is fetched live once and cached under `evals/fixtures/private/`.

Arms, all on the same pool and the unmodified agent3 selector (`select_with_runners_up`, both passes):
  baseline   summarize every article, select from the summaries           (today, AGENT2B_MODE=all)
  repeat     select again from the same summaries: Haiku's own selection noise, the floor to beat
  shortlist  stage 1 on lead text -> keep picks + runners-up -> select from their summaries
             (AGENT2B_MODE=shortlist). Summaries are reused from the baseline arm, so this isolates what
             the shortlist does to the picks; its token cost is modelled from the measured numbers.

Metric: of the baseline's picks, the share that the other arm also picks (a pick-level rate with a
Wilson interval). The shortlist is acceptable when that share is not distinguishable from the
repeat arm's. `shortlist_recall` is the share of baseline picks that survive stage 1 at all.
Duplicate removal is left out on purpose: it follows selection and is evaluated separately.
"""
import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))
os.chdir(ROOT)  # agents open prompts/... relative to the repo root
os.environ["LANGSMITH_TRACING"] = "false"

import agent2b_summarize_news as a2b  # noqa: E402
import agent3_compose as a3  # noqa: E402
import config  # noqa: E402
import news_shortlist as ns  # noqa: E402
from article_fetch import fetch_article_text  # noqa: E402
from evals import cost, results  # noqa: E402
from evals.snapshot import FIXTURES_DIR, PRIVATE_DIR  # noqa: E402
from evals.stats import rate_with_ci  # noqa: E402

FROZEN_PATH = FIXTURES_DIR / "articles_frozen.json"
TEXT_CACHE = PRIVATE_DIR / "shortlist_eval_texts.json"
SUMMARY_CACHE = PRIVATE_DIR / "shortlist_eval_summaries.json"

# Per-call budgets for the pre-run estimate only (actual usage is metered).
_SUMMARY_TOKENS_IN = 1250     # measured in evals/fixtures/summaries_frozen.json
_SUMMARY_TOKENS_OUT = 80
_SELECT_TOKENS_PER_ARTICLE = 150
_SELECT_OVERHEAD = 1200


# ---------------------------------------------------------------------------
# Pure helpers (unit tested)
# ---------------------------------------------------------------------------

def retained(base: dict, other: dict) -> tuple[int, int]:
    """(k, n): of the baseline's picks ({key: [urls]}), how many are also picked in `other`."""
    k = n = 0
    for key, urls in base.items():
        theirs = set(other.get(key, []))
        n += len(urls)
        k += sum(1 for u in urls if u in theirs)
    return k, n


def picks_by_key(selected: dict) -> dict:
    """{(category, pass): [Article...]} -> {"category|pass": [urls]}."""
    return {f"{cat}|{p}": [a["url"] for a in arts] for (cat, p), arts in selected.items()}


def stage1_survivors(base: dict, shortlisted_urls: set) -> tuple[int, int]:
    """(k, n): baseline picks that are inside the stage-1 shortlist."""
    urls = [u for v in base.values() for u in v]
    return sum(1 for u in urls if u in shortlisted_urls), len(urls)


def load_pool(path=FROZEN_PATH) -> dict:
    """{category: [article dict in agent2b's shape]} from the frozen snapshot."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    pool: dict = {}
    for a in doc["articles"]:
        cat = a.get("final_category")
        if not cat:
            continue
        pool.setdefault(cat, []).append({
            "source": a.get("source", ""), "title": a["title"], "url": a["url"],
            "description": a.get("snippet", "") or "", "language": a.get("language", "en"),
            "hn_score": a.get("hn_score")})
    return pool


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------

class Meter:
    def __init__(self):
        self.lock = threading.Lock()
        self.input = self.output = self.calls = 0

    def record(self, response):
        u = getattr(response, "usage", None)
        with self.lock:
            self.calls += 1
            self.input += getattr(u, "input_tokens", 0) or 0
            self.output += getattr(u, "output_tokens", 0) or 0


class _Msgs:
    def __init__(self, inner, meter):
        self._inner, self._meter = inner, meter

    def create(self, **kw):
        r = self._inner.create(**kw)
        self._meter.record(r)
        return r


class Metered:
    def __init__(self, client, meter):
        self.messages = _Msgs(client.messages, meter)


def _estimate(n_articles: int, n_categories: int, cached_summaries: int) -> float:
    to_summarize = max(0, n_articles - cached_summaries)
    s_in = to_summarize * _SUMMARY_TOKENS_IN
    s_out = to_summarize * _SUMMARY_TOKENS_OUT
    passes = 2 * n_categories  # all + English-only
    sel_in = 3 * (n_articles * _SELECT_TOKENS_PER_ARTICLE * 2 // 2 + passes * _SELECT_OVERHEAD)  # base, repeat, stage 1
    sel_out = 3 * passes * 60
    return cost.estimate_cost(s_in + sel_in, s_out + sel_out, config.SCORING_MODEL)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _select_all(pool_by_cat: dict, prompt: str, client) -> dict:
    """agent3's two passes per category -> {(category, 'all'|'en'): [articles]}."""
    jobs = []
    for cat, arts in pool_by_cat.items():
        if not arts:
            continue
        jobs.append((cat, "all", arts))
        en = [a for a in arts if a.get("language") != "fr"]
        if 0 < len(en) < len(arts):
            jobs.append((cat, "en", en))
        elif en:
            jobs.append((cat, "en", arts))

    def one(job):
        cat, p, arts = job
        return (cat, p), a3.select_with_runners_up(cat, list(arts), prompt, client).picks

    with ThreadPoolExecutor(max_workers=4) as ex:
        return dict(ex.map(one, jobs))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--approve", action="store_true", help="only after the owner OKs an estimate over 2 USD")
    args = ap.parse_args(argv)

    pool = load_pool()
    n = sum(len(v) for v in pool.values())
    cached = _load_json(SUMMARY_CACHE)
    est = _estimate(n, len(pool), len(cached))
    print(f"{n} articles in {len(pool)} categories; {len(cached)} summaries cached. Estimated cost ${est:.2f}")
    if args.dry_run:
        return 0
    cost.CostGuard(approved=args.approve).check(est)

    import anthropic
    key = os.environ.get("ANTHROPIC_1ST_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_1ST_API_KEY is not set")
    real = anthropic.Anthropic(api_key=key, timeout=60.0)
    meter = Meter()
    client = Metered(real, meter)

    templates = {k: Path("prompts", f"{k}.txt").read_text(encoding="utf-8")
                 for k in ("news_summary_prompt", "news_summary_fallback_prompt", "quebec_french_style",
                           "article_selection_prompt")}

    # 1. page text (live, cached)
    texts = _load_json(TEXT_CACHE)
    missing = [a["url"] for arts in pool.values() for a in arts if a["url"] not in texts]
    if missing:
        PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=a2b.MAX_FETCH_WORKERS) as ex:
            for url, text in zip(missing, ex.map(fetch_article_text, missing)):
                texts[url] = text
        TEXT_CACHE.write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")
    print(f"page text: {sum(1 for t in texts.values() if t)}/{len(texts)} fetched", flush=True)

    # 2. baseline summaries (cached; the agent2b function, unmodified)
    todo = [a for arts in pool.values() for a in arts if a["url"] not in cached]

    def summarize(a):
        m = Meter()
        r = a2b.summarize_article(a, texts.get(a["url"]), templates["news_summary_prompt"],
                                  templates["news_summary_fallback_prompt"], templates["quebec_french_style"],
                                  Metered(real, m))
        return a["url"], {"summary": r.get("summary"), "used_fallback": r.get("used_fallback"),
                          "in": m.input, "out": m.output}

    with ThreadPoolExecutor(max_workers=3) as ex:
        for url, rec in ex.map(summarize, todo):
            cached[url] = rec
    SUMMARY_CACHE.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
    summarized = {cat: [{**a, "summary": cached[a["url"]]["summary"],
                         "used_fallback": cached[a["url"]]["used_fallback"]} for a in arts]
                  for cat, arts in pool.items()}
    print(f"summaries ready: {sum(1 for c in cached.values() if c['summary'])}/{len(cached)}", flush=True)

    sel_prompt = templates["article_selection_prompt"]

    # 3. baseline + repeat selection
    sel_meter_before = (meter.input, meter.output)
    base = picks_by_key(_select_all(summarized, sel_prompt, client))
    repeat = picks_by_key(_select_all(summarized, sel_prompt, client))
    per_selection = ((meter.input - sel_meter_before[0]) // 2, (meter.output - sel_meter_before[1]) // 2)

    # 4. stage 1 on lead text, then stage 2 on the shortlisted summaries
    flat_texts, by_cat_list, offset = {}, {}, 0
    for cat, arts in pool.items():
        by_cat_list[cat] = arts
        for i, a in enumerate(arts):
            flat_texts[offset + i] = texts.get(a["url"])
        offset += len(arts)
    s1_before = (meter.input, meter.output)
    kept, summary = ns.shortlist_all(by_cat_list, flat_texts, sel_prompt, client)
    stage1_tokens = (meter.input - s1_before[0], meter.output - s1_before[1])

    flat = [a for arts in pool.values() for a in arts]
    kept_urls = {flat[i]["url"] for i in kept}
    shortlisted = {cat: [a for a in arts if a["url"] in kept_urls] for cat, arts in summarized.items()}
    s2_before = (meter.input, meter.output)
    short = picks_by_key(_select_all(shortlisted, sel_prompt, client))
    stage2_tokens = (meter.input - s2_before[0], meter.output - s2_before[1])

    # 5. metrics
    k_rep, n_rep = retained(base, repeat)
    k_sh, n_sh = retained(base, short)
    k_rec, n_rec = stage1_survivors(base, kept_urls)
    summ_in = sum(cached[u]["in"] for u in kept_urls if u in cached)
    summ_out = sum(cached[u]["out"] for u in kept_urls if u in cached)
    base_summ_in = sum(c["in"] for c in cached.values())
    base_summ_out = sum(c["out"] for c in cached.values())
    cost_all = cost.estimate_cost(base_summ_in + per_selection[0], base_summ_out + per_selection[1], config.SCORING_MODEL)
    cost_shortlist = cost.estimate_cost(stage1_tokens[0] + summ_in + stage2_tokens[0],
                                        stage1_tokens[1] + summ_out + stage2_tokens[1], config.SCORING_MODEL)
    metrics = {
        "baseline_picks_kept_by_repeat": rate_with_ci(k_rep, n_rep),
        "baseline_picks_kept_by_shortlist": rate_with_ci(k_sh, n_sh),
        "shortlist_recall_of_baseline_picks": rate_with_ci(k_rec, n_rec),
        "articles_shortlisted": {"value": len(kept), "n": n, "ci_low": None, "ci_high": None},
        "cost_per_run_all_usd": {"value": round(cost_all, 4), "n": n, "ci_low": None, "ci_high": None},
        "cost_per_run_shortlist_usd": {"value": round(cost_shortlist, 4), "n": n, "ci_low": None, "ci_high": None},
    }
    actual = cost.estimate_cost(meter.input, meter.output, config.SCORING_MODEL)
    doc = results.build_results(
        f"shortlist_eval_{date.today().isoformat()}", config.SCORING_MODEL, metrics, cost_usd=round(actual, 4),
        notes=("Frozen 500-article pool, unmodified agent3 selector. Shortlist summaries are reused from the "
               "baseline arm; shortlist cost is modelled from measured tokens. Duplicate removal excluded."))
    path = results.write_results(doc)
    rows = {"shortlist_summary": summary, "baseline": base, "repeat": repeat, "shortlist": short}
    path.with_name(path.stem + "_rows.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    for name, m in metrics.items():
        print(f"  {name}: {m['value']}" + (f" [{m['ci_low']:.3f}, {m['ci_high']:.3f}]" if m.get("ci_low") is not None else ""))
    print(f"wrote {path.relative_to(ROOT)}; actual eval cost ${actual:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
