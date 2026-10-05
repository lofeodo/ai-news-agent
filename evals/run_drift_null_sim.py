r"""Estimate the drift check's false-alarm rate (and power for one planted shift) by simulation.

No API calls, no network, cost 0 USD. Each simulated week is 500-ish articles bootstrapped from
a real week's audit rows (`data/agent1b_review_log.json`), so the null is "same distribution
every week". Real weeks vary more than a bootstrap of one week does, so the false-alarm rate
reported here is a LOWER bound on what production will see.

CMD:  venv\Scripts\python -m evals.run_drift_null_sim [--weeks 500] [--seed 0]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
import config  # noqa: E402
from drift import evaluate, summarize_audit  # noqa: E402
from evals.results import build_results, write_results  # noqa: E402
from evals.stats import rate_with_ci  # noqa: E402

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "agent1b_review_log.json"
KW = dict(min_runs=config.DRIFT_MIN_BASELINE_RUNS, p_threshold=config.DRIFT_P_THRESHOLD,
          min_ks_d=config.DRIFT_KS_MIN_D, min_share_shift=config.DRIFT_MIN_SHARE_SHIFT,
          min_rate_shift=config.DRIFT_MIN_RATE_SHIFT)


def _week(rng, rows, shift_to=None, shift_frac=0.0):
    """Bootstrap one week from `rows`; optionally relabel a fraction of articles to `shift_to`."""
    idx = rng.integers(0, len(rows), len(rows))
    sample = [dict(rows[i]) for i in idx]
    if shift_to:
        for r in sample:
            if rng.random() < shift_frac:
                r["final_category"] = shift_to
    s = summarize_audit(sample)
    s["articles"] = len(sample)
    s["routed_to_review"] = sum(1 for r in sample if r["routed_to_review"])
    return s


def simulate(rows, weeks, seed, shift_to=None, shift_frac=0.0):
    rng = np.random.default_rng(seed)
    flagged = {"confidence": 0, "category_mix": 0, "review_rate": 0, "any": 0}
    for _ in range(weeks):
        baseline = [_week(rng, rows) for _ in range(config.DRIFT_BASELINE_RUNS)]
        out = evaluate(_week(rng, rows, shift_to, shift_frac), baseline, **KW)
        hit = {r["metric"] for r in out["results"] if r["status"] == "drift"}
        for m in hit:
            flagged[m] += 1
        flagged["any"] += bool(hit)
    return flagged


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=500)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    rows = json.loads(LOG_PATH.read_text(encoding="utf-8"))["articles"]
    cats = sorted({r["final_category"] for r in rows})
    target = cats[0]  # planted shift: 10% of articles relabeled into one category
    null = simulate(rows, args.weeks, args.seed)
    planted = {f: simulate(rows, args.weeks, args.seed + 1, shift_to=target, shift_frac=f) for f in (0.10, 0.20)}

    metrics = {f"null_false_alarm_{m}": rate_with_ci(k, args.weeks) for m, k in null.items()}
    for frac, flagged in planted.items():
        metrics[f"planted_{int(frac * 100)}pct_category_shift_detected"] = rate_with_ci(flagged["category_mix"], args.weeks)
    notes = (f"Bootstrap of one real week ({len(rows)} articles) used as the null for {args.weeks} simulated weeks, "
             f"each judged against 4 simulated prior weeks. Real week-to-week variation is larger than a bootstrap of "
             f"one week, so the false-alarm rates are a lower bound. Planted shifts: 10% and 20% of articles relabeled into "
             f"{target!r} (the 10% case sits right at the {config.DRIFT_MIN_SHARE_SHIFT:.0%} effect floor, so low power there is expected). Thresholds from config.py: p<{config.DRIFT_P_THRESHOLD}, KS D>={config.DRIFT_KS_MIN_D}, "
             f"share shift>={config.DRIFT_MIN_SHARE_SHIFT}, rate shift>={config.DRIFT_MIN_RATE_SHIFT}.")
    path = write_results(build_results("drift_null_simulation", model="none", metrics=metrics, cost_usd=0.0, notes=notes))
    for name, m in metrics.items():
        print(f"{name}: {m['value']:.3f} (n={m['n']}, 95% CI {m['ci_low']:.3f}-{m['ci_high']:.3f})")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
