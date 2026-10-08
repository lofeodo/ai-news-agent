"""Scoring for the agent3 dedup eval (control vs Haiku vs Sonnet). Pure functions, no API calls.

A *row* is one labelled case:
    case_id, kind, category, article_ids, hn {id: score|None}, gold_groups [[id, ...]], borderline [id, ...],
    runs {arm: [run, ...]}
and a *run* is one repeat of one arm on that case:
    groups [[id, ...]]   duplicate groups the arm reported (empty for the control arm)
    removed [id, ...]    articles the keep policy dropped (empty for the control arm)
    failed (bool), input_tokens, output_tokens, seconds

Borderline articles (`t` in the labels: same topic, different angle) are scored two ways. Strict treats them
as unique, so grouping or removing one counts as a mistake. Broad ignores any pair or removal involving one.
"""
from itertools import combinations

from evals.stats import paired_wins_losses, rate_with_ci

CONTROL = "control"


def prepare_cases(cases, gold):
    """(rows, articles): one row per case with `?` articles dropped, and {case_id: [article dicts]} to send."""
    rows, articles = [], {}
    for c in cases:
        cid = c["case_id"]
        drop = gold.excluded.get(cid, set())
        arts = [a for a in c["articles"] if a["id"] not in drop]
        ids = [a["id"] for a in arts]
        rows.append({
            "case_id": cid, "kind": c.get("kind", ""), "category": c.get("category", ""),
            "article_ids": ids, "hn": {a["id"]: a.get("hn_score") for a in arts},
            "gold_groups": [sorted(g) for g in gold.groups.get(cid, [])],
            "borderline": sorted(gold.borderline.get(cid, set()) & set(ids)),
            "runs": {},
        })
        articles[cid] = arts
    return rows, articles


def _pairs(groups):
    return {frozenset(p) for g in groups for p in combinations(sorted(g), 2)}


def _gold_pairs(row):
    return _pairs(row["gold_groups"])


def _removable(row):
    return sum(len(g) - 1 for g in row["gold_groups"])


def _wrongly_removed(row, run, broad):
    """Articles the run removed that have no kept gold duplicate (or, strict, are borderline)."""
    removed = set(run["removed"])
    border = set(row["borderline"])
    wrong = set()
    for a in removed:
        if broad and a in border:
            continue
        mates = [set(g) for g in row["gold_groups"] if a in g]
        if not mates or not (mates[0] - removed - {a}):
            wrong.add(a)
    return wrong


def _runs(rows, arm, kind=None):
    for r in rows:
        if kind is None or r["kind"] == kind:
            for run in r["runs"].get(arm, []):
                yield r, run


def recall(rows, arm, kind=None):
    hit = total = 0
    for r, run in _runs(rows, arm, kind):
        gp = _gold_pairs(r)
        hit += len(gp & _pairs(run["groups"]))
        total += len(gp)
    return rate_with_ci(hit, total)


def false_group_rate(rows, arm, broad=False, kind=None):
    """Predicted pairs that are not duplicates in the gold, over all predicted pairs."""
    bad = total = 0
    for r, run in _runs(rows, arm, kind):
        gp, border = _gold_pairs(r), set(r["borderline"])
        for p in _pairs(run["groups"]):
            if broad and p & border:
                continue
            total += 1
            bad += p not in gp
    return rate_with_ci(bad, total)


def false_removal_rate(rows, arm, broad=False, kind=None):
    """Wrongly removed articles over the articles that should have been kept."""
    bad = total = 0
    for r, run in _runs(rows, arm, kind):
        border = set(r["borderline"]) if broad else set()
        total += len(r["article_ids"]) - _removable(r) - len(border)
        bad += len(_wrongly_removed(r, run, broad))
    return rate_with_ci(bad, max(total, 0))


def residual_duplicate_rate(rows, arm, kind=None):
    """Gold groups that still have 2+ articles after removal."""
    left = total = 0
    for r, run in _runs(rows, arm, kind):
        removed = set(run["removed"])
        for g in r["gold_groups"]:
            total += 1
            left += len(set(g) - removed) >= 2
    return rate_with_ci(left, total)


def case_exact_match(rows, arm):
    ok = n = 0
    for r, run in _runs(rows, arm):
        n += 1
        ok += {frozenset(g) for g in run["groups"]} == {frozenset(g) for g in r["gold_groups"]}
    return rate_with_ci(ok, n)


def failure_rate(rows, arm):
    runs = [run for _, run in _runs(rows, arm)]
    return rate_with_ci(sum(bool(run.get("failed")) for run in runs), len(runs))


def _measure(value, n):
    return {"value": value, "n": n, "ci_low": None, "ci_high": None}


def usage_metrics(rows, arm):
    runs = [run for _, run in _runs(rows, arm)]
    if not runs:
        return {}
    secs = sorted(run.get("seconds", 0.0) for run in runs)
    p95 = secs[min(len(secs) - 1, int(0.95 * len(secs)))]
    n = len(runs)
    return {
        f"tokens_input__{arm}": _measure(sum(r.get("input_tokens", 0) for r in runs), n),
        f"tokens_output__{arm}": _measure(sum(r.get("output_tokens", 0) for r in runs), n),
        f"seconds_mean__{arm}": _measure(round(sum(secs) / n, 3), n),
        f"seconds_p95__{arm}": _measure(round(p95, 3), n),
    }


def _outcomes(rows, arm, kind):
    """Per-item booleans in a fixed order (case, repeat, item), so two arms line up for a paired test."""
    out = []
    for r in rows:
        runs = r["runs"].get(arm, [])
        for run in runs:
            if kind == "recall":
                pred = _pairs(run["groups"])
                out += [p in pred for p in sorted(_gold_pairs(r), key=sorted)]
            else:
                wrong = _wrongly_removed(r, run, broad=False)
                gold_removed = {a for g in r["gold_groups"] for a in g}
                keep = [a for a in r["article_ids"] if a not in gold_removed]
                out += [a not in wrong for a in keep]
    return out


def paired_metric(rows, arm_a, arm_b, kind, name):
    """B vs A per item. value = share of discordant items that B wins (Wilson CI over discordant items)."""
    a, b = _outcomes(rows, arm_a, kind), _outcomes(rows, arm_b, kind)
    if len(a) != len(b):
        return {}
    p = paired_wins_losses(a, b)
    metric = rate_with_ci(p["wins"], p["wins"] + p["losses"])
    metric.update(wins=p["wins"], losses=p["losses"], ties=p["ties"], items=p["n"])
    return {name: metric}


def arms_present(rows):
    return sorted({a for r in rows for a in r["runs"]}, key=lambda a: (a != CONTROL, a))


def _arm_metrics(rows, arm):
    out = {
        f"duplicate_recall__{arm}": recall(rows, arm),
        f"false_removal_rate_strict__{arm}": false_removal_rate(rows, arm),
        f"false_removal_rate_broad__{arm}": false_removal_rate(rows, arm, broad=True),
        f"false_group_rate_strict__{arm}": false_group_rate(rows, arm),
        f"false_group_rate_broad__{arm}": false_group_rate(rows, arm, broad=True),
        f"residual_duplicate_rate__{arm}": residual_duplicate_rate(rows, arm),
        f"case_exact_match__{arm}": case_exact_match(rows, arm),
        f"failure_rate__{arm}": failure_rate(rows, arm),
    }
    for kind in sorted({r["kind"] for r in rows if r["kind"]}):
        out[f"duplicate_recall__{arm}__{kind}"] = recall(rows, arm, kind)
        out[f"false_removal_rate_strict__{arm}__{kind}"] = false_removal_rate(rows, arm, kind=kind)
        out[f"residual_duplicate_rate__{arm}__{kind}"] = residual_duplicate_rate(rows, arm, kind=kind)
    out.update(usage_metrics(rows, arm))
    return out


def verdict(rows, cheap, strong):
    """Plain-language model choice from the Wilson intervals; honest about the small gold set."""
    if cheap not in arms_present(rows) or strong not in arms_present(rows):
        return "Only one model ran, so no model comparison was made."
    rc, rs = recall(rows, cheap), recall(rows, strong)
    fc, fs = false_removal_rate(rows, cheap), false_removal_rate(rows, strong)
    summary = (f"recall {cheap} {rc['value']:.2f} ({rc['ci_low']:.2f}-{rc['ci_high']:.2f}) vs {strong} "
               f"{rs['value']:.2f} ({rs['ci_low']:.2f}-{rs['ci_high']:.2f}); false removal {cheap} "
               f"{fc['value']:.3f} vs {strong} {fs['value']:.3f}")
    if rs["ci_low"] > rc["ci_high"] and fs["value"] <= fc["value"]:
        return f"Use {strong}: clearly higher recall without more false removals ({summary})."
    if rc["ci_low"] > rs["ci_high"] and fc["value"] <= fs["value"]:
        return f"Use {cheap}: clearly higher recall without more false removals ({summary})."
    return (f"No clear winner ({summary}); the Wilson 95% intervals overlap, so prefer the cheaper {cheap} "
            "unless its false removals are worse. The gold set has few duplicate groups, so intervals are wide.")


def score(rows, cheap="haiku", strong="sonnet"):
    """(metrics, verdict) for a results file."""
    metrics = {}
    arms = arms_present(rows)
    for arm in arms:
        metrics.update(_arm_metrics(rows, arm))
    if cheap in arms and strong in arms:
        metrics.update(paired_metric(rows, cheap, strong, "recall", f"paired_recall_{strong}_vs_{cheap}"))
        metrics.update(paired_metric(rows, cheap, strong, "removal",
                                     f"paired_false_removal_{strong}_vs_{cheap}"))
    return metrics, verdict(rows, cheap, strong)
