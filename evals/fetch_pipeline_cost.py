"""Read-only: summarize Claude spend per pipeline step from the `llm_usage` archive on Firestore run docs.

The weekly health check copies each run's LangSmith token and cost totals onto `pipeline_runs/{run_id}.llm_usage`
(agents/usage_archive.py). This averages the archived runs per agent and writes evals/results/pipeline_cost.json,
which evals.make_charts draws. Cost is LangSmith's estimate from its price list, not an invoice; nothing is
written to Firestore.

CMD:  venv\\Scripts\\python -m evals.fetch_pipeline_cost                      (archived runs; needs Application Default Credentials)
      venv\\Scripts\\python -m evals.fetch_pipeline_cost --run <run_id> [...]  (these runs, read from LangSmith directly;
                                                                               needs LANGSMITH_API_KEY, writes nothing to Firestore)

`--run` exists so a run the health check has not archived (a debug run, or one still inside LangSmith's 14-day window)
can be measured. Calls LangSmith cannot price (DeepSeek) are priced from agents/pricing.py (usage_archive).
"""
import argparse
import os
import statistics
import sys

from evals import results

AGENTS = ("agent1a", "agent1b", "agent2a", "agent2b", "agent3")


def _models_label(usages):
    models = sorted({m for u in usages for m in (u.get("models") or [])})
    return " + ".join(models) if models else "claude-haiku-4-5"


def summarize(usages):
    """metrics from a list of `llm_usage` dicts. value = mean per run; ci_low/ci_high = min/max seen (n runs)."""
    n = len(usages)

    def metric(values):
        return {"value": statistics.fmean(values), "n": n, "ci_low": min(values), "ci_high": max(values)}

    metrics = {}
    for a in AGENTS:
        rows = [u["agents"].get(a, {}) for u in usages]
        metrics[f"cost_usd__{a}"] = metric([r.get("cost", 0.0) for r in rows])
        metrics[f"calls__{a}"] = metric([r.get("calls", 0) for r in rows])
        metrics[f"tokens__{a}"] = metric([r.get("input", 0) + r.get("output", 0) for r in rows])
    metrics["cost_usd__total"] = metric([u["total"]["cost"] for u in usages])
    metrics["calls__total"] = metric([u["total"]["calls"] for u in usages])
    return metrics


def _archived(limit):
    from google.cloud import firestore
    db = firestore.Client(project=os.environ.get("GCP_PROJECT_ID") or "ai-news-letter-497720")
    query = db.collection("pipeline_runs").order_by("started_at", direction=firestore.Query.DESCENDING).limit(limit)
    return [x["llm_usage"] for x in (d.to_dict() for d in query.stream())
            if x.get("llm_usage", {}).get("agents") and x["llm_usage"]["total"]["calls_without_cost"] == 0]


def _from_langsmith(run_ids):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents"))
    import usage_archive
    from langsmith import Client
    if not os.environ.get("LANGSMITH_API_KEY"):
        sys.exit("LANGSMITH_API_KEY is not set")
    os.environ.setdefault("LANGSMITH_PROJECT", "latent-spacemail-prod")
    ls = Client()
    usages = []
    for run_id in run_ids:
        u = usage_archive.fetch_run_usage(ls, run_id)
        if u is None:
            sys.exit(f"no traced calls for run {run_id} in LangSmith project {os.environ['LANGSMITH_PROJECT']}")
        if u["total"]["calls_without_cost"]:
            sys.exit(f"run {run_id}: {u['total']['calls_without_cost']} calls could not be priced")
        u["models"] = sorted({usage_archive._model_of(r) or "?" for r in usage_archive._list_llm_runs(ls, run_id)})
        for a, row in u["agents"].items():
            print(f"  {run_id} {a}: {row['calls']} calls, {row['input']:,} in / {row['output']:,} out, "
                  f"${row['cost']:.4f}" + (f" ({row['calls_priced_locally']} priced locally)" if row["calls_priced_locally"] else ""))
        usages.append(u)
    return usages


def main(argv=None, limit=30):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", nargs="+", metavar="RUN_ID", help="measure these runs from LangSmith instead of the archive")
    ap.add_argument("--note", default="", help="extra sentence for the results file (e.g. what configuration ran)")
    args = ap.parse_args(argv)

    usages = _from_langsmith(args.run) if args.run else _archived(limit)
    if not usages:
        print("no archived llm_usage found")
        return 1
    n = len(usages)
    source = (f"{'This' if n == 1 else 'These'} {n} run{'s' if n > 1 else ''} read from LangSmith ({', '.join(args.run)})"
              if args.run else f"Mean over {n} archived runs")
    doc = results.build_results(
        "pipeline_cost", _models_label(usages), summarize(usages), cost_usd=0.0,
        notes=f"{source} (min and max in place of an interval). List-price estimate for model calls only "
              "(LangSmith's for Claude, agents/pricing.py for DeepSeek); hosting, SendGrid and the proxy are not "
              f"included. {args.note}".strip())
    print(f"wrote {results.write_results(doc)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
