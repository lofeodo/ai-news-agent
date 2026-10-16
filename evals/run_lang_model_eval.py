"""Model comparison for agent1b's language filter: Haiku vs DeepSeek. Makes REAL API calls. Never collected by pytest.

CMD (needs ANTHROPIC_1ST_API_KEY and DEEPSEEK_API_KEY):
    venv\\Scripts\\python -m evals.run_lang_model_eval --dry-run
    venv\\Scripts\\python -m evals.run_lang_model_eval

Same frozen pool and batches as evals/run_lang_filter_eval (a fresh unfiltered fetch after the prefilter,
cached under evals/fixtures/private/). Arms, all through the production `language_filter_batch`:
  haiku_full     AGENT1B_LANG_MODE=claude       the reference: Haiku labels every article
  haiku_exc      AGENT1B_LANG_MODE=exceptions   what production runs today (since 2026-10-16)
  ds_exc_a/b     AGENT1B_LANG_MODE=exceptions   MODEL_LANG_FILTER=deepseek-flash, twice (its own noise)
  ds_full        AGENT1B_LANG_MODE=claude       MODEL_LANG_FILTER=deepseek-flash
The owner's rule (unchanged): against the reference, foreign-language articles let through must not rise and
English or French articles wrongly dropped must not rise. Disagreements are written to a rows file to settle by hand.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from evals import run_lang_filter_eval as base  # sets up sys.path, cwd, tracing off

import agent1b_fetch_news as a1b  # noqa: E402
import config  # noqa: E402
import llm  # noqa: E402
import pricing  # noqa: E402
from evals import cost, results  # noqa: E402
from evals.snapshot import PRIVATE_DIR  # noqa: E402
from evals.stats import rate_with_ci  # noqa: E402

ROOT = base.ROOT
DS_MODEL = "deepseek-flash"

ARMS = (  # name, AGENT1B_LANG_MODE, model for the stage ("" = Claude default), pricing key
    ("haiku_full", "claude", "", config.SCORING_MODEL),
    ("haiku_exc", "exceptions", "", config.SCORING_MODEL),
    ("ds_exc_a", "exceptions", DS_MODEL, DS_MODEL),
    ("ds_exc_b", "exceptions", DS_MODEL, DS_MODEL),
    ("ds_full", "claude", DS_MODEL, DS_MODEL),
)


def run_arm(mode, model, batches, client):
    os.environ["AGENT1B_LANG_MODE"] = mode
    if model:
        os.environ["MODEL_LANG_FILTER"] = model
    else:
        os.environ.pop("MODEL_LANG_FILTER", None)
    meter = base.Meter()
    metered = base.Metered(client, meter)
    labels, failed = {}, 0
    with ThreadPoolExecutor(max_workers=a1b.MAX_CONCURRENT_CLAUDE_CALLS) as ex:
        futures = [ex.submit(a1b.language_filter_batch, b, i + 1, metered) for i, b in enumerate(batches)]
        for batch, fut in zip(batches, futures):
            try:
                labels.update(base.labels_from_kept(batch, fut.result()))
            except Exception as e:  # a failed batch is a measured failure, not a crash
                failed += 1
                print(f"  batch failed: {type(e).__name__}: {str(e)[:120]}")
                labels.update({a["url"]: "failed" for a in batch})
    return labels, meter, failed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    pool = base.load_pool()
    batches = base.make_batches(pool)
    est = base.estimate(len(pool)) * 1.4  # Haiku arms dominate; DeepSeek arms are a few cents
    print(f"{len(pool)} articles ({len(batches)} batches). Estimated cost ${est:.2f}")
    if args.dry_run:
        return 0
    cost.CostGuard(approved=False).check(est)

    import anthropic
    claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_1ST_API_KEY"], timeout=60.0)
    ds = anthropic.Anthropic(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=llm.DEEPSEEK_BASE_URL, timeout=60.0)
    client = llm.RoutedClient(claude, ds)

    arms, meters, failed = {}, {}, {}
    for name, mode, model, _ in ARMS:
        arms[name], meters[name], failed[name] = run_arm(mode, model, batches, client)
        m = meters[name]
        print(f"{name}: {m.calls} calls, {m.input} in / {m.output} out tokens, {failed[name]} failed batches, "
              f"{llm.fallbacks} fallbacks so far", flush=True)

    n = len(pool)
    ref = arms["haiku_full"]
    metrics = {}
    for name in ("haiku_exc", "ds_exc_a", "ds_exc_b", "ds_full"):
        c = base.compare(ref, arms[name])
        for k in ("foreign_through", "wrongly_dropped", "relabelled"):
            metrics[f"{name}_vs_ref_{k}"] = rate_with_ci(len(c[k]), n)
    cc = base.compare(arms["ds_exc_a"], arms["ds_exc_b"])
    for k in ("foreign_through", "wrongly_dropped", "relabelled"):
        metrics[f"ds_exc_b_vs_a_{k}"] = rate_with_ci(len(cc[k]), n)
    prices = {name: price for name, _, _, price in ARMS}
    total = 0.0
    for name, m in meters.items():
        usd = pricing.estimate_cost(m.input, m.output, prices[name])
        total += usd
        metrics[f"cost_usd_{name}"] = {"value": round(usd, 4), "n": n, "ci_low": None, "ci_high": None}
        metrics[f"tokens_{name}"] = {"value": [m.input, m.output], "n": n, "ci_low": None, "ci_high": None}
    metrics["fallbacks_to_claude"] = {"value": llm.fallbacks, "n": n, "ci_low": None, "ci_high": None}

    doc = results.build_results(
        f"lang_model_eval_{date.today().isoformat()}", f"{config.SCORING_MODEL} vs {DS_MODEL}", metrics,
        cost_usd=round(total, 4),
        notes=("Same frozen pool and batches per arm. Reference = Haiku labelling every article. DeepSeek priced off-peak. "
               "No hand labels: disagreements are in the rows file. Cost per arm is modelled from measured tokens."))
    path = results.write_results(doc)
    rows = base.disagreements(arms)
    by_url = {a["url"]: a for a in pool}
    for r in rows:
        a = by_url[r["url"]]
        r.update(title=a.get("title"), domain=a1b.urlparse(a["url"]).hostname, description=(a.get("description") or "")[:200])
    rows_path = PRIVATE_DIR / f"lang_model_eval_{date.today().isoformat()}_rows.json"
    rows_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for k, v in metrics.items():
        print(f"  {k}: {v['value']}")
    print(f"{len(rows)} articles with disagreement -> {rows_path.relative_to(ROOT)}; wrote {path.relative_to(ROOT)}; "
          f"eval cost ${total:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
