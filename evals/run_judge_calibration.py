"""On-demand calibration of the faithfulness judge against the owner's `supported` labels.

Makes REAL Claude calls (the judge model). Never collected by pytest.

CMD:
    venv\\Scripts\\python -m evals.run_judge_calibration --dry-run     (counts + cost estimate, no API calls)
    venv\\Scripts\\python -m evals.run_judge_calibration
    venv\\Scripts\\python -m evals.run_judge_calibration --approve     (only after the owner OKs an estimate over 2 USD)

Reads evals/labels/summaries_template.csv (the `supported` column, filled by hand) and the source text
under evals/fixtures/private/summary_sources/ (rebuilt by make_label_templates.py summaries). Writes
evals/results/judge_calibration.json and judge_calibration_rows.json (ids, labels, verdicts; no text).

Limits to keep in mind when reading the result: n=40; the judge is the same model family as the
summarizer; the labeler saw the same source and summary; the source was re-fetched when the template was
built, so it may differ slightly from what the summarizer saw; and unsupported summaries are probably rare,
which makes recall and kappa very uncertain.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))
os.chdir(ROOT)

import config  # noqa: E402
import judge  # noqa: E402
import online_judge  # noqa: E402
from evals import cost, judge_eval, results  # noqa: E402

EVALS_DIR = ROOT / "evals"
NAME = "judge_calibration"


def read_source(row):
    return (EVALS_DIR / row["source_text_ref"]).read_text(encoding="utf-8")


def run_calibration(rows, client, workers=5):
    """Judge every labeled row. Returns ({id: bool | None}, [row detail], usage)."""
    template = judge.load_prompt()
    usage = {"input": 0, "output": 0}

    def one(r):
        try:
            v = judge.judge_summary(client, r["title"], read_source(r), r["generated_summary"], template=template)
            return r["id"], v
        except Exception as e:
            return r["id"], {"error": f"{type(e).__name__}: {e}"}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(one, rows))
    verdicts, details = {}, []
    for (rid, v), r in zip(out, rows):
        verdicts[rid] = v.get("supported")
        usage["input"] += v.get("input_tokens", 0)
        usage["output"] += v.get("output_tokens", 0)
        details.append({"id": rid, "kind": r["kind"], "source_kind": r["source_kind"], "gold_supported": r["gold"],
                        "judge_supported": v.get("supported"), "n_claims": len(v.get("unsupported_claims", [])),
                        "claims": [c[:200] for c in v.get("unsupported_claims", [])], "error": v.get("error")})
    return verdicts, details, usage


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--allow-partial", action="store_true", help="score only the rows labeled so far")
    args = ap.parse_args(argv)

    rows = judge_eval.load_gold()
    labeled = [r for r in rows if r["gold"] is not None]
    if len(labeled) < len(rows) and not args.allow_partial and not args.dry_run:
        print(f"{len(rows) - len(labeled)} of {len(rows)} rows have no `supported` label yet; finish labeling "
              f"(or pass --allow-partial). Nothing was called.")
        return 1
    if args.dry_run and len(labeled) < len(rows):
        print(f"({len(rows) - len(labeled)} rows are not labeled yet; estimating over all {len(rows)})")
        labeled = rows
    missing = [r["id"] for r in labeled if not (EVALS_DIR / r["source_text_ref"]).exists()]
    if missing:
        print(f"source text missing for {len(missing)} rows (rebuild with make_label_templates.py summaries): {missing[:5]}")
        return 1

    est = sum(online_judge.estimate_item_cost(read_source(r), r["generated_summary"]) for r in labeled)
    print(f"rows to judge: {len(labeled)}; model {config.JUDGE_MODEL}; estimated cost ${est:.3f}")
    if args.dry_run:
        return 0
    cost.CostGuard(approved=args.approve).check(est)

    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_1ST_API_KEY"))
    verdicts, details, usage = run_calibration(labeled, client)
    errors = [d for d in details if d["error"]]
    if errors:
        print(f"{len(errors)} of {len(details)} judge calls errored; first: {errors[0]['error'][:300]}")
    metrics, detail = judge_eval.score(labeled, verdicts)
    actual = cost.estimate_cost(usage["input"], usage["output"], model=config.JUDGE_MODEL)
    reading = " ".join(judge_eval.verdict_lines(metrics, detail))
    notes = (f"Judge {config.JUDGE_MODEL} vs the owner's `supported` labels on {detail['scored']} summaries "
             f"({detail['judge_errors']} judge errors). Same model family as the summarizer; n is small; the labeler "
             f"saw the same source and summary; sources were re-fetched when the template was built. {reading}")
    doc = results.build_results(NAME, config.JUDGE_MODEL, metrics, cost_usd=round(actual, 4), notes=notes)
    doc["detail"] = detail
    path = results.write_results(doc)
    path.with_name(f"{NAME}_rows.json").write_text(json.dumps(details, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}; cost ${actual:.3f}")
    for line in judge_eval.verdict_lines(metrics, detail):
        print(" ", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
