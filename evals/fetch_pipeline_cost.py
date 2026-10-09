"""Read-only: summarize Claude spend per pipeline step from the `llm_usage` archive on Firestore run docs.

The weekly health check copies each run's LangSmith token and cost totals onto `pipeline_runs/{run_id}.llm_usage`
(agents/usage_archive.py). This averages the archived runs per agent and writes evals/results/pipeline_cost.json,
which evals.make_charts draws. Cost is LangSmith's estimate from its price list, not an invoice; nothing is
written to Firestore.

CMD:  venv\\Scripts\\python -m evals.fetch_pipeline_cost   (needs Application Default Credentials)
"""
import os
import statistics

from evals import results

AGENTS = ("agent1a", "agent1b", "agent2a", "agent2b", "agent3")


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


def main(limit=30):
    from google.cloud import firestore
    db = firestore.Client(project=os.environ.get("GCP_PROJECT_ID") or "ai-news-letter-497720")
    query = db.collection("pipeline_runs").order_by("started_at", direction=firestore.Query.DESCENDING).limit(limit)
    usages = [x["llm_usage"] for x in (d.to_dict() for d in query.stream())
              if x.get("llm_usage", {}).get("agents") and x["llm_usage"]["total"]["calls_without_cost"] == 0]
    if not usages:
        print("no archived llm_usage found")
        return 1
    doc = results.build_results(
        "pipeline_cost", "claude-haiku-4-5", summarize(usages), cost_usd=0.0,
        notes=f"Mean over {len(usages)} archived runs (min and max in place of an interval). LangSmith's price "
              "estimate for Claude calls only; hosting, SendGrid and the proxy are not included.")
    print(f"wrote {results.write_results(doc)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
