"""Scoring for the agent1b review-step eval (single_pass vs graph). Pure functions, no API calls.

A *row* is one gold-labelled article with every variant's prediction:
    id, stratum, gold, single_pass, first_pass, confidence, final, routed (bool), tool_calls,
    input_tokens, output_tokens, review_status
`first_pass`/`confidence` come from the graph's categorize call; `final` is after the review loop.
"""
import csv
from pathlib import Path

from scipy.stats import spearmanr

from evals.stats import paired_wins_losses, rate_with_ci, wilson_interval

CONFIDENCE_BUCKETS = (1, 2, 3, 4, 5)


def load_gold(path):
    """{article id: {"gold", "stratum", "url"}} for rows the owner labelled; blank rows are skipped."""
    gold = {}
    with open(Path(path), encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            label = (r.get("gold_category") or "").strip()
            if label:
                gold[r["id"]] = {"gold": label, "stratum": r.get("sample_stratum", ""), "url": r.get("url", "")}
    return gold


def join(gold, predictions):
    """Rows for every gold article that has a prediction. `predictions`: {id: {...row fields...}}."""
    return [{"id": i, "stratum": g["stratum"], "gold": g["gold"], **predictions[i]}
            for i, g in gold.items() if i in predictions]


def _accuracy(rows, field):
    return rate_with_ci(sum(1 for r in rows if r[field] == r["gold"]), len(rows))


def accuracy_metrics(rows):
    """Accuracy per variant, overall and per sampling stratum."""
    out = {}
    for field, label in (("single_pass", "single_pass"), ("first_pass", "graph_first_pass"), ("final", "graph_final")):
        out[f"accuracy_{label}"] = _accuracy(rows, field)
        for stratum in sorted({r["stratum"] for r in rows if r["stratum"]}):
            out[f"accuracy_{label}__{stratum}"] = _accuracy([r for r in rows if r["stratum"] == stratum], field)
    return out


def calibration_metrics(rows):
    """Graph first-pass accuracy per verbalized-confidence bucket, plus a rank correlation."""
    out = {}
    for b in CONFIDENCE_BUCKETS:
        bucket = [r for r in rows if r["confidence"] == b]
        if bucket:
            out[f"calibration_first_pass__conf_{b}"] = _accuracy(bucket, "first_pass")
    scored = [r for r in rows if r["confidence"] is not None]
    correct = [int(r["first_pass"] == r["gold"]) for r in scored]
    conf = [r["confidence"] for r in scored]
    if len(set(conf)) > 1 and len(set(correct)) > 1:
        rho = float(spearmanr(conf, correct).statistic)
    else:
        rho = None
    out["confidence_correctness_spearman"] = {"value": rho, "n": len(scored), "ci_low": None, "ci_high": None}
    return out


def confidence_verdict(rows, threshold):
    """Plain-language statement on whether confidence predicts errors, from Wilson intervals."""
    low = [r for r in rows if r["confidence"] is not None and r["confidence"] < threshold]
    high = [r for r in rows if r["confidence"] is not None and r["confidence"] >= threshold]
    if not low or not high:
        return f"Cannot assess confidence: {len(low)} rows below threshold {threshold}, {len(high)} at or above."
    lo_k = sum(r["first_pass"] == r["gold"] for r in low)
    hi_k = sum(r["first_pass"] == r["gold"] for r in high)
    lo_ci, hi_ci = wilson_interval(lo_k, len(low)), wilson_interval(hi_k, len(high))
    summary = (f"first-pass accuracy {lo_k}/{len(low)} below threshold {threshold} vs {hi_k}/{len(high)} at or above")
    if hi_ci[0] > lo_ci[1]:
        return f"Confidence predicts errors: {summary} (Wilson 95% intervals do not overlap)."
    return (f"Verbalized confidence does NOT clearly predict errors: {summary}, "
            "and the Wilson 95% intervals overlap.")


def routing_metrics(rows):
    """Review cost: share routed, tool calls and tokens spent on routed articles."""
    routed = [r for r in rows if r["routed"]]
    n = len(rows)
    out = {"share_routed_to_review": rate_with_ci(len(routed), n)}
    if routed:
        m = len(routed)
        out["mean_tool_calls_per_reviewed"] = {
            "value": sum(r["tool_calls"] for r in routed) / m, "n": m, "ci_low": None, "ci_high": None}
        out["review_input_tokens_per_reviewed"] = {
            "value": sum(r["input_tokens"] for r in routed) / m, "n": m, "ci_low": None, "ci_high": None}
        out["review_output_tokens_per_reviewed"] = {
            "value": sum(r["output_tokens"] for r in routed) / m, "n": m, "ci_low": None, "ci_high": None}
        failed = sum(1 for r in routed if r.get("review_status") == "review_failed")
        out["review_degraded_share"] = rate_with_ci(failed, m)
    return out


def paired_metric(rows, a_field, b_field, name):
    """B vs A per-item. value = share of discordant pairs that B wins (Wilson CI over discordant pairs)."""
    a = [r[a_field] == r["gold"] for r in rows]
    b = [r[b_field] == r["gold"] for r in rows]
    p = paired_wins_losses(a, b)
    discordant = p["wins"] + p["losses"]
    metric = rate_with_ci(p["wins"], discordant)
    metric.update(wins=p["wins"], losses=p["losses"], ties=p["ties"], items=p["n"])
    return {name: metric}


def paired_metrics(rows):
    out = {}
    out.update(paired_metric(rows, "single_pass", "final", "paired_graph_final_vs_single_pass"))
    out.update(paired_metric(rows, "first_pass", "final", "paired_review_effect"))
    return out


def score(rows, threshold):
    """All metrics for a results file, plus the calibration verdict (goes in the notes)."""
    metrics = {}
    for part in (accuracy_metrics(rows), calibration_metrics(rows), routing_metrics(rows), paired_metrics(rows)):
        metrics.update(part)
    return metrics, confidence_verdict(rows, threshold)
