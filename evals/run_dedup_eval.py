"""On-demand dedup eval: control vs Haiku vs Sonnet on the frozen, gold-labelled dedup cases.

Makes REAL Claude calls. Never collected by pytest.

CMD:
    venv\\Scripts\\python -m evals.run_dedup_eval --dry-run     (counts + cost estimate, no API calls)
    venv\\Scripts\\python -m evals.run_dedup_eval               (refuses if the estimate exceeds 2 USD)
    venv\\Scripts\\python -m evals.run_dedup_eval --approve     (only after the owner OKs the estimate)

Each model arm runs the production first-turn check (DedupConversation.start) on every case, then the
production keep policy (highest HN score, ties to the earliest). The control arm removes nothing. The refill
path (add) is not scored. A DedupError on a case is recorded as a failed run (no groups, nothing removed).
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))
os.chdir(ROOT)  # agents open prompts/... relative to the repo root

import config  # noqa: E402
from agent3_dedup_graph import keep_index  # noqa: E402
from dedup import DedupConversation, DedupError  # noqa: E402
from evals import cost, dedup_eval, results  # noqa: E402
from evals.dedup_labels import load_all_gold  # noqa: E402
from evals.make_dedup_cases import CASES_PATH, SHIPPED_CASES_PATH  # noqa: E402

ARM_MODELS = {"haiku": config.SCORING_MODEL, "sonnet": config.JUDGE_MODEL}
ALL_ARMS = (dedup_eval.CONTROL, *ARM_MODELS)
MAX_WORKERS = 5

# Rough budgets used only for the pre-run estimate (actual usage is metered per call).
_PROMPT_OVERHEAD_TOKENS = 600
_TOKENS_PER_ARTICLE = 110       # title + 300-char summary + tags
_OUT_TOKENS_PER_CASE = 120
_TOKENIZER_FACTOR = {"sonnet": 1.3}   # Sonnet 5.5's tokenizer uses ~30% more tokens


def load_cases(paths=(CASES_PATH, SHIPPED_CASES_PATH)):
    cases = []
    for p in paths:
        cases += json.loads(Path(p).read_text(encoding="utf-8"))["cases"]
    return cases


def estimate_tokens(articles_by_case, arm, repeats):
    """(input_tokens, output_tokens) worst case for one model arm."""
    n_in = sum(_PROMPT_OVERHEAD_TOKENS + _TOKENS_PER_ARTICLE * len(a) for a in articles_by_case.values())
    n_out = _OUT_TOKENS_PER_CASE * len(articles_by_case)
    f = _TOKENIZER_FACTOR.get(arm, 1.0)
    return int(n_in * f * repeats), int(n_out * f * repeats)


def estimate_cost(articles_by_case, arms, repeats):
    total = 0.0
    for arm in arms:
        if arm == dedup_eval.CONTROL:
            continue
        tin, tout = estimate_tokens(articles_by_case, arm, repeats)
        total += cost.estimate_cost(tin, tout, model=ARM_MODELS[arm])
    return total


def _one_run(client, arm, row, articles):
    """One repeat of one model arm on one case."""
    t0 = time.monotonic()
    conv = DedupConversation(client, row["category"], model=ARM_MODELS[arm])
    try:
        groups = conv.start(articles)
        failed = False
    except DedupError:
        groups, failed = [], True
    removed = []
    for g in groups:
        kept = keep_index(g.indices, articles)
        removed += [articles[i]["id"] for i in g.indices if i != kept]
    return {"groups": [[articles[i]["id"] for i in g.indices] for g in groups], "removed": removed,
            "failed": failed, "input_tokens": conv.usage["input_tokens"],
            "output_tokens": conv.usage["output_tokens"], "seconds": round(time.monotonic() - t0, 3)}


def run_eval(rows, articles_by_case, client, arms, repeats):
    """Fills row["runs"][arm] for every case; returns the rows."""
    jobs = []
    for row in rows:
        for arm in arms:
            row["runs"][arm] = []
            for _ in range(repeats if arm != dedup_eval.CONTROL else 1):
                jobs.append((arm, row))
    empty = {"groups": [], "removed": [], "failed": False, "input_tokens": 0, "output_tokens": 0, "seconds": 0.0}

    def go(job):
        arm, row = job
        if arm == dedup_eval.CONTROL:
            return dict(empty)
        return _one_run(client, arm, row, articles_by_case[row["case_id"]])

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for (arm, row), res in zip(jobs, pool.map(go, jobs)):
            row["runs"][arm].append(res)
    # control is deterministic: repeat it so paired items line up with the model arms
    if dedup_eval.CONTROL in arms:
        for row in rows:
            row["runs"][dedup_eval.CONTROL] = row["runs"][dedup_eval.CONTROL] * repeats
    return rows


def total_cost(rows):
    total = 0.0
    for arm, model in ARM_MODELS.items():
        runs = [run for r in rows for run in r["runs"].get(arm, [])]
        total += cost.estimate_cost(sum(x["input_tokens"] for x in runs),
                                    sum(x["output_tokens"] for x in runs), model=model)
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print counts and the cost estimate, call nothing")
    ap.add_argument("--approve", action="store_true", help="owner approved an estimate above the 2 USD limit")
    ap.add_argument("--name", default=f"dedup_eval_{date.today().isoformat()}")
    ap.add_argument("--arms", default=",".join(ALL_ARMS), help="comma list from: " + ", ".join(ALL_ARMS))
    ap.add_argument("--repeats", type=int, default=3, help="runs per case per model arm (default 3)")
    args = ap.parse_args(argv)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    bad = [a for a in arms if a not in ALL_ARMS]
    if bad:
        ap.error(f"unknown arm(s): {', '.join(bad)}")
    if args.repeats < 1:
        ap.error("--repeats must be at least 1")

    gold = load_all_gold()
    rows, articles_by_case = dedup_eval.prepare_cases(load_cases(), gold)
    est = estimate_cost(articles_by_case, arms, args.repeats)
    n_groups = sum(len(g) for g in gold.groups.values())
    print(f"cases: {len(rows)}; articles: {sum(len(a) for a in articles_by_case.values())}; "
          f"gold groups: {n_groups} in {len(gold.groups)} cases; borderline articles: "
          f"{sum(len(b) for b in gold.borderline.values())}")
    print(f"arms: {', '.join(arms)}; repeats per model arm: {args.repeats}")
    print(f"estimated worst-case cost: ${est:.3f} "
          f"(models: {', '.join(ARM_MODELS[a] for a in arms if a in ARM_MODELS) or 'none'})")
    if args.dry_run:
        return 0
    cost.CostGuard(approved=args.approve).check(est)

    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_1ST_API_KEY"))
    rows = run_eval(rows, articles_by_case, client, arms, args.repeats)

    cheap, strong = "haiku", "sonnet"
    metrics, verdict = dedup_eval.score(rows, cheap, strong)
    actual = total_cost(rows)
    notes = (f"{verdict} {args.repeats} repeats per model arm, pooled; repeats of one case are not independent, "
             "so the Wilson intervals are optimistic. Only the first-turn check (start) is scored; the refill "
             "path is not. Strict treats borderline `t` articles as unique, broad ignores pairs and removals "
             "involving them. Fixtures hold 300-char snippets and summaries, as the detector saw in production.")
    doc = results.build_results(args.name, " vs ".join(ARM_MODELS[a] for a in arms if a in ARM_MODELS),
                                metrics, cost_usd=round(actual, 4), notes=notes)
    path = results.write_results(doc)
    rows_path = path.with_name(f"{args.name}_rows.json")
    rows_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path} and {rows_path.name}; cost ${actual:.3f}")
    print(verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
