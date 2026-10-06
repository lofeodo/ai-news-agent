"""Pure scoring for the faithfulness judge's calibration against the owner's `supported` labels.

No API calls here (see run_judge_calibration.py). The positive class for precision and recall is
"unsupported", because that is what the weekly check exists to catch.
"""
import csv
import random
from pathlib import Path

from evals import stats

LABELS_PATH = Path(__file__).parent / "labels" / "summaries_template.csv"

_YES = {"yes", "y", "true", "1", "supported", "s"}
_NO = {"no", "n", "false", "0", "unsupported", "u"}

# Common reading of kappa (Landis and Koch); a convention, not a law. Used only to word the verdict.
KAPPA_ACCEPTABLE = 0.6


def parse_label(value):
    """True for supported, False for unsupported, None for blank. Anything else raises."""
    v = (value or "").strip().lower()
    if not v:
        return None
    if v in _YES:
        return True
    if v in _NO:
        return False
    raise ValueError(f"unrecognised `supported` label {value!r}; use yes/no")


def load_gold(path=LABELS_PATH):
    """Rows of the labels CSV with `gold` (bool or None when not labeled yet)."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["gold"] = parse_label(r.get("supported"))
    return rows


def bootstrap_kappa_ci(gold, judged, n_boot=2000, seed=0):
    """Percentile 95% interval for Cohen's kappa. Resamples where kappa is undefined (one class only) are skipped."""
    rng = random.Random(seed)
    n, vals = len(gold), []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        g, j = [gold[i] for i in idx], [judged[i] for i in idx]
        if len(set(g) | set(j)) < 2:
            continue
        k = stats.cohens_kappa(g, j)
        if k == k:  # not NaN
            vals.append(k)
    if len(vals) < n_boot // 2:
        return (None, None)
    vals.sort()
    return (vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1])


def score(rows, verdicts):
    """rows: gold rows (with `gold`), verdicts: {id: bool | None} (None = judge errored).

    Returns (metrics, detail). Only rows that have both a gold label and a verdict are scored; the
    others are counted in `detail`.
    """
    scored = [(r, verdicts[r["id"]]) for r in rows if r["gold"] is not None and verdicts.get(r["id"]) is not None]
    gold = [r["gold"] for r, _ in scored]
    judged = [v for _, v in scored]
    n = len(scored)
    detail = {"rows": len(rows), "labeled": sum(1 for r in rows if r["gold"] is not None), "scored": n,
              "judge_errors": sum(1 for r in rows if r["gold"] is not None and verdicts.get(r["id"]) is None)}
    if n == 0:
        raise ValueError("no rows with both a gold label and a judge verdict")

    metrics = {}
    agree = sum(g == j for g, j in zip(gold, judged))
    metrics["agreement"] = stats.rate_with_ci(agree, n)

    tp = sum(1 for g, j in zip(gold, judged) if not g and not j)   # unsupported, judge says unsupported
    fn = sum(1 for g, j in zip(gold, judged) if not g and j)       # unsupported, judge says supported
    fp = sum(1 for g, j in zip(gold, judged) if g and not j)       # supported, judge says unsupported
    tn = sum(1 for g, j in zip(gold, judged) if g and j)
    detail["confusion"] = {"gold_unsupported_judge_unsupported": tp, "gold_unsupported_judge_supported": fn,
                           "gold_supported_judge_unsupported": fp, "gold_supported_judge_supported": tn}
    detail["gold_unsupported"] = tp + fn
    detail["gold_supported"] = fp + tn
    if tp + fn:
        metrics["unsupported_recall"] = stats.rate_with_ci(tp, tp + fn)
    if tp + fp:
        metrics["unsupported_precision"] = stats.rate_with_ci(tp, tp + fp)
    if fp + tn:
        metrics["false_alarm_rate"] = stats.rate_with_ci(fp, fp + tn)

    if len(set(gold)) < 2 or len(set(judged)) < 2:
        detail["kappa_note"] = "kappa is undefined: gold labels or judge verdicts contain a single class"
    else:
        lo, hi = bootstrap_kappa_ci(gold, judged)
        metrics["cohens_kappa"] = {"value": stats.cohens_kappa(gold, judged), "n": n, "ci_low": lo, "ci_high": hi}

    for kind in sorted({r["source_kind"] for r, _ in scored}):
        sub = [(g, j) for (r, _), g, j in zip(scored, gold, judged) if r["source_kind"] == kind]
        metrics[f"agreement_{kind}"] = stats.rate_with_ci(sum(g == j for g, j in sub), len(sub))
    return metrics, detail


def verdict_lines(metrics, detail):
    """Plain-language reading to print and to put in the results notes."""
    lines = []
    k = metrics.get("cohens_kappa")
    if k is None:
        lines.append(f"kappa not computable ({detail.get('kappa_note', 'no data')}).")
    else:
        lo, hi = k["ci_low"], k["ci_high"]
        ci = f"{lo:.2f} to {hi:.2f}" if lo is not None else "unavailable"
        ok = "meets" if k["value"] >= KAPPA_ACCEPTABLE else "is below"
        lines.append(f"kappa {k['value']:.2f} (bootstrap 95% CI {ci}, n={k['n']}) {ok} the {KAPPA_ACCEPTABLE} "
                     f"working bar; the interval is wide at this n.")
    lines.append(f"gold: {detail['gold_supported']} supported, {detail['gold_unsupported']} unsupported "
                 f"(n={detail['scored']}); with so few unsupported rows, recall is very uncertain.")
    return lines
