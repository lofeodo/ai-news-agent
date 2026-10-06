r"""Estimate the judge's unsupported-rate drift test's false-alarm rate and power by simulation.

No API calls, no network, cost 0 USD. There is no history of real weekly unsupported rates yet, so the
baseline rate is ASSUMED at a few levels; each simulated week judges `JUDGE_MAX_ITEMS` summaries (binomial)
and is tested against 4 simulated prior weeks with the production rule (`drift.evaluate_judge`: Fisher
p < DRIFT_P_THRESHOLD and a rise of at least JUDGE_MIN_UNSUPPORTED_SHIFT). Read the results as "how the rule
behaves if the true unsupported rate is about X", not as a measured production rate. With about a dozen items
a week the test can only catch a large jump, which is the point of reporting it.

CMD:  venv\Scripts\python -m evals.run_judge_drift_sim [--weeks 2000] [--seed 0]
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
import config  # noqa: E402
from drift import evaluate_judge  # noqa: E402
from evals.results import build_results, write_results  # noqa: E402
from evals.stats import rate_with_ci  # noqa: E402

KW = dict(min_runs=config.DRIFT_MIN_BASELINE_RUNS, p_threshold=config.DRIFT_P_THRESHOLD,
          min_shift=config.JUDGE_MIN_UNSUPPORTED_SHIFT)
BASE_RATES = (0.05, 0.15, 0.30)
PLANTED_FROM = 0.10   # baseline rate for the power runs
PLANTED_TO = (0.30, 0.50, 0.70)


def flag_count(base_rate, cur_rate, weeks, seed):
    rng = np.random.default_rng(seed)
    n = config.JUDGE_MAX_ITEMS
    hits = 0
    for _ in range(weeks):
        base = [{"judged": n, "unsupported": int(rng.binomial(n, base_rate))} for _ in range(config.DRIFT_BASELINE_RUNS)]
        cur = {"judged": n, "unsupported": int(rng.binomial(n, cur_rate))}
        hits += evaluate_judge(cur, base, **KW)["status"] == "drift"
    return hits


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    metrics = {}
    for r in BASE_RATES:
        metrics[f"null_false_alarm_at_{int(r * 100)}pct_unsupported"] = rate_with_ci(
            flag_count(r, r, args.weeks, args.seed), args.weeks)
    for to in PLANTED_TO:
        metrics[f"planted_{int(PLANTED_FROM * 100)}_to_{int(to * 100)}pct_detected"] = rate_with_ci(
            flag_count(PLANTED_FROM, to, args.weeks, args.seed + 1), args.weeks)

    notes = (f"Simulated weeks of {config.JUDGE_MAX_ITEMS} judged summaries each (binomial), tested against "
             f"{config.DRIFT_BASELINE_RUNS} simulated prior weeks of the same size. The true unsupported rate is ASSUMED "
             f"(see each metric name); no real weekly rate exists yet, so these are not measured production rates. Rule: "
             f"Fisher exact p < {config.DRIFT_P_THRESHOLD} and a rise of at least {config.JUDGE_MIN_UNSUPPORTED_SHIFT:.0%}. "
             f"Perfect judge assumed: judge error is not modelled.")
    path = write_results(build_results("judge_drift_simulation", model="none", metrics=metrics, cost_usd=0.0, notes=notes))
    for name, m in metrics.items():
        print(f"{name}: {m['value']:.3f} (n={m['n']}, 95% CI {m['ci_low']:.3f}-{m['ci_high']:.3f})")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
