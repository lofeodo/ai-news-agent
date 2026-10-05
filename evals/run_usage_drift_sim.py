r"""Estimate the token/cost drift test's false-alarm rate and power by simulation.

No API calls, no network, cost 0 USD. There is no history of real week-to-week token variation
yet (usage archiving starts with the first deployed run), so the false-alarm rate is reported
at several ASSUMED noise levels (coefficient of variation of weekly total tokens), centred on the
token count of the one real agent1b run in `data/agent1b_review_log.json`. Read the results as
"how the rule behaves if weeks vary by about X%", not as a measured production rate. Power is
shown for planted multiplicative jumps at a mid noise level.

CMD:  venv\Scripts\python -m evals.run_usage_drift_sim [--weeks 2000] [--seed 0]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
import config  # noqa: E402
from drift import evaluate_usage, usage_totals  # noqa: E402
from evals.results import build_results, write_results  # noqa: E402
from evals.stats import rate_with_ci  # noqa: E402

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "agent1b_review_log.json"
KW = dict(min_runs=config.DRIFT_MIN_BASELINE_RUNS, min_ratio=config.DRIFT_USAGE_MIN_RATIO,
          min_tokens=config.DRIFT_USAGE_MIN_TOKENS, min_usd=config.DRIFT_USAGE_MIN_USD)
NOISE_LEVELS = (0.05, 0.10, 0.20, 0.30)
PLANTED_JUMPS = (0.30, 0.60, 1.00)
PLANTED_NOISE = 0.10


def _week(rng, center, cv, jump=0.0):
    tokens = max(1.0, rng.normal(center, cv * center)) * (1 + jump)
    inp, out = int(tokens * 0.9), int(tokens * 0.1)
    cost = inp / 1e6 * 1.0 + out / 1e6 * 5.0
    return usage_totals({"agents": {"agent1b": {"input": inp, "output": out, "calls": 1}},
                         "total": {"input": inp, "output": out, "calls": 1, "cost": cost, "calls_without_cost": 0}})


def flag_rate(center, cv, weeks, seed, jump=0.0):
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(weeks):
        base = [_week(rng, center, cv) for _ in range(config.DRIFT_BASELINE_RUNS)]
        hits += evaluate_usage(_week(rng, center, cv, jump), base, **KW)["status"] == "drift"
    return hits


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    summary = json.loads(LOG_PATH.read_text(encoding="utf-8"))["summary"]["token_usage"]
    center = sum(n["input"] + n["output"] for n in summary.values())

    metrics = {}
    for cv in NOISE_LEVELS:
        metrics[f"null_false_alarm_noise_{int(cv * 100)}pct"] = rate_with_ci(
            flag_rate(center, cv, args.weeks, args.seed), args.weeks)
    for jump in PLANTED_JUMPS:
        metrics[f"planted_{int(jump * 100)}pct_jump_detected_at_noise_{int(PLANTED_NOISE * 100)}pct"] = rate_with_ci(
            flag_rate(center, PLANTED_NOISE, args.weeks, args.seed + 1, jump), args.weeks)

    notes = (f"Simulated weekly total tokens centred on {center:,} (the token_usage of one real agent1b run). Each simulated "
             f"week is judged against {config.DRIFT_BASELINE_RUNS} simulated prior weeks. Noise is ASSUMED (normal, coefficient of "
             f"variation shown in each metric name) because no real week-to-week variation exists yet; this is not a measured "
             f"production false-alarm rate. Rule: flag at >={config.DRIFT_USAGE_MIN_RATIO:.0%} from the prior median and "
             f">={config.DRIFT_USAGE_MIN_TOKENS:,} tokens / ${config.DRIFT_USAGE_MIN_USD:.2f}. Total tokens and cost move together "
             f"in this simulation, so any flag counts once.")
    path = write_results(build_results("usage_drift_simulation", model="none", metrics=metrics, cost_usd=0.0, notes=notes))
    for name, m in metrics.items():
        print(f"{name}: {m['value']:.3f} (n={m['n']}, 95% CI {m['ci_low']:.3f}-{m['ci_high']:.3f})")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
