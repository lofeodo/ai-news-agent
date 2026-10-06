"""Pure drift-monitoring helpers: no Firestore, no network, no I/O.

scipy is imported lazily inside the statistical tests so that agent1b, which only needs
`summarize_audit`, does not pay the import cost.
"""
from collections import Counter

CONFIDENCE_KEYS = ["1", "2", "3", "4", "5", "none"]


def summarize_audit(audit: list[dict]) -> dict:
    """Small per-run distribution summary from agent1b's per-article audit rows.

    Keys are strings so the dict round-trips through Firestore unchanged.
    """
    hist = {k: 0 for k in CONFIDENCE_KEYS}
    for row in audit:
        c = row.get("confidence")
        key = str(c) if isinstance(c, int) and 1 <= c <= 5 else "none"
        hist[key] += 1
    categories = Counter(r["final_category"] for r in audit if r.get("final_category"))
    return {"confidence_hist": hist, "category_counts": dict(categories)}


def _pool_hists(runs: list[dict]) -> dict:
    pooled = {k: 0 for k in CONFIDENCE_KEYS}
    for r in runs:
        for k, v in (r.get("confidence_hist") or {}).items():
            pooled[k] = pooled.get(k, 0) + v
    return pooled


def _pool_counts(runs: list[dict]) -> Counter:
    pooled = Counter()
    for r in runs:
        pooled.update(r.get("category_counts") or {})
    return pooled


def _expand_confidence(hist: dict) -> list[int]:
    # "none" (no confidence returned) is excluded: it is not a point on the 1-5 scale.
    return [int(k) for k in CONFIDENCE_KEYS[:5] for _ in range(hist.get(k, 0))]


def confidence_ks(current: dict, baseline: dict, p_threshold: float, min_d: float) -> dict:
    """Two-sample KS on 1-5 confidence (this week vs pooled baseline).

    The scale is discrete with heavy ties, so the asymptotic p-value is approximate;
    the effect-size floor on D does the real work of keeping the flag meaningful.
    """
    from scipy.stats import ks_2samp

    cur, base = _expand_confidence(current), _expand_confidence(baseline)
    if len(cur) < 2 or len(base) < 2:
        return {"metric": "confidence", "status": "insufficient_data", "n_current": len(cur), "n_baseline": len(base)}
    res = ks_2samp(cur, base, method="asymp")
    d, p = float(res.statistic), float(res.pvalue)
    return {"metric": "confidence", "status": "drift" if p < p_threshold and d >= min_d else "ok",
            "statistic": d, "p_value": p, "n_current": len(cur), "n_baseline": len(base),
            "mean_current": sum(cur) / len(cur), "mean_baseline": sum(base) / len(base)}


def _chi2_stat(table) -> float:
    import numpy as np

    table = np.asarray(table, dtype=float)
    expected = table.sum(1, keepdims=True) * table.sum(0, keepdims=True) / table.sum()
    return float(((table - expected) ** 2 / expected).sum())


def category_mix_test(current: dict, baseline: dict, p_threshold: float, min_shift: float,
                      permutations: int = 5000, seed: int = 0) -> dict:
    """Category mix, this week vs pooled baseline.

    Chi-square test of homogeneity; when any expected count is under 5 (rare
    categories) a seeded permutation test is used instead, since the chi-square
    approximation is unreliable there. Also reports the largest per-category share shift.
    """
    import numpy as np
    from scipy.stats import chi2_contingency

    cats = sorted(set(current) | set(baseline))
    cur = np.array([current.get(c, 0) for c in cats])
    base = np.array([baseline.get(c, 0) for c in cats])
    keep = (cur + base) > 0
    cats, cur, base = [c for c, k in zip(cats, keep) if k], cur[keep], base[keep]
    n_cur, n_base = int(cur.sum()), int(base.sum())
    if n_cur == 0 or n_base == 0 or len(cats) < 2:
        return {"metric": "category_mix", "status": "insufficient_data", "n_current": n_cur, "n_baseline": n_base}

    shifts = {c: float(cur[i] / n_cur - base[i] / n_base) for i, c in enumerate(cats)}
    top = max(shifts, key=lambda c: abs(shifts[c]))
    table = np.vstack([cur, base])
    expected = table.sum(1, keepdims=True) * table.sum(0, keepdims=True) / table.sum()
    if expected.min() >= 5:
        method, p = "chi2", float(chi2_contingency(table, correction=False)[1])
    else:
        method = "permutation"
        rng = np.random.default_rng(seed)
        labels = np.repeat(np.arange(len(cats)), cur + base)
        observed = _chi2_stat(table)
        hits = 0
        for _ in range(permutations):
            rng.shuffle(labels)
            counts = np.bincount(labels[:n_cur], minlength=len(cats))
            hits += _chi2_stat(np.vstack([counts, (cur + base) - counts])) >= observed - 1e-9
        p = (hits + 1) / (permutations + 1)
    return {"metric": "category_mix", "status": "drift" if p < p_threshold and abs(shifts[top]) >= min_shift else "ok",
            "method": method, "p_value": p, "n_current": n_cur, "n_baseline": n_base,
            "largest_shift_category": top, "largest_shift": shifts[top]}


def review_rate_test(cur_routed: int, cur_n: int, base_routed: int, base_n: int,
                     p_threshold: float, min_shift: float) -> dict:
    """Share of articles routed to the review loop: Fisher exact, this week vs pooled baseline."""
    from scipy.stats import fisher_exact

    if cur_n == 0 or base_n == 0:
        return {"metric": "review_rate", "status": "insufficient_data", "n_current": cur_n, "n_baseline": base_n}
    p = float(fisher_exact([[cur_routed, cur_n - cur_routed], [base_routed, base_n - base_routed]])[1])
    rc, rb = cur_routed / cur_n, base_routed / base_n
    return {"metric": "review_rate", "status": "drift" if p < p_threshold and abs(rc - rb) >= min_shift else "ok",
            "p_value": p, "n_current": cur_n, "n_baseline": base_n, "rate_current": rc, "rate_baseline": rb}


def evaluate(current: dict, baseline_runs: list[dict], *, min_runs: int, p_threshold: float,
             min_ks_d: float, min_share_shift: float, min_rate_shift: float) -> dict:
    """Compare this week's summary with the prior runs' summaries.

    Each run dict carries `confidence_hist`, `category_counts`, `articles`, `routed_to_review`.
    Returns {"status": "insufficient_history" | "ok" | "drift", "baseline_runs": n, "results": [...]}.
    """
    if len(baseline_runs) < min_runs:
        return {"status": "insufficient_history", "baseline_runs": len(baseline_runs), "results": []}
    results = [
        confidence_ks(current.get("confidence_hist") or {}, _pool_hists(baseline_runs), p_threshold, min_ks_d),
        category_mix_test(current.get("category_counts") or {}, dict(_pool_counts(baseline_runs)),
                          p_threshold, min_share_shift),
        review_rate_test(current.get("routed_to_review", 0), current.get("articles", 0),
                         sum(r.get("routed_to_review", 0) for r in baseline_runs),
                         sum(r.get("articles", 0) for r in baseline_runs), p_threshold, min_rate_shift),
    ]
    status = "drift" if any(r["status"] == "drift" for r in results) else "ok"
    return {"status": status, "baseline_runs": len(baseline_runs), "results": results}


# --- Token and cost drift -------------------------------------------------------------------

def usage_totals(usage: dict) -> dict:
    """Tokens and costs from an archived `llm_usage` dict (see agents/usage_archive.py).

    `langsmith_cost` is None unless LangSmith priced every call; `table_cost` is our list-price
    estimate from the token counts (agents/pricing.py). `cost` is what drift tests use: LangSmith's
    figure when complete, otherwise the estimate.
    """
    import pricing

    agents = {}
    for name, a in (usage.get("agents") or {}).items():
        agents[name] = {
            "tokens": a.get("input", 0) + a.get("output", 0),
            "table_cost": pricing.estimate_cost(a.get("input", 0), a.get("output", 0)),
        }
    total = usage.get("total") or {}
    complete = total.get("calls", 0) > 0 and total.get("calls_without_cost", 0) == 0
    ls_cost = total.get("cost") if complete else None
    table_cost = sum(a["table_cost"] for a in agents.values())
    return {
        "tokens": total.get("input", 0) + total.get("output", 0),
        "input": total.get("input", 0), "output": total.get("output", 0), "calls": total.get("calls", 0),
        "langsmith_cost": ls_cost, "table_cost": table_cost,
        "cost": ls_cost if ls_cost is not None else table_cost,
        "agents": agents,
    }


def usage_ratio_test(name: str, current: float, baseline: list[float], min_ratio: float, min_abs: float) -> dict:
    """Flag `current` if it is at least `min_ratio` away from the baseline median and the absolute
    change is at least `min_abs`. Medians resist one odd prior week; no p-value (too few weeks)."""
    import statistics

    if not baseline:
        return {"metric": name, "status": "insufficient_data"}
    median = statistics.median(baseline)
    change = current - median
    ratio = (change / median) if median else None
    drift = abs(change) >= min_abs and (ratio is None or abs(ratio) >= min_ratio)
    return {"metric": name, "status": "drift" if drift else "ok",
            "current": current, "median": median, "ratio": ratio}


def evaluate_usage(current: dict, baseline_usages: list[dict], *, min_runs: int, min_ratio: float,
                   min_tokens: float, min_usd: float) -> dict:
    """Compare this run's usage with prior runs. Inputs are `usage_totals()` dicts."""
    if len(baseline_usages) < min_runs:
        return {"status": "insufficient_history", "baseline_runs": len(baseline_usages), "results": []}
    results = [
        usage_ratio_test("total tokens", current["tokens"], [b["tokens"] for b in baseline_usages], min_ratio, min_tokens),
        usage_ratio_test("total cost", current["cost"], [b["cost"] for b in baseline_usages], min_ratio, min_usd),
    ]
    for agent, a in sorted(current["agents"].items()):
        prior = [b["agents"][agent]["tokens"] for b in baseline_usages if agent in b["agents"]]
        if len(prior) >= min_runs:
            results.append(usage_ratio_test(f"{agent} tokens", a["tokens"], prior, min_ratio, min_tokens))
    status = "drift" if any(r["status"] == "drift" for r in results) else "ok"
    return {"status": status, "baseline_runs": len(baseline_usages), "results": results}


# --- Summary faithfulness (online judge) ----------------------------------------------------

def judge_unsupported_test(cur_bad: int, cur_n: int, base_bad: int, base_n: int,
                           p_threshold: float, min_shift: float) -> dict:
    """Share of judged summaries flagged unsupported: Fisher exact, this week vs pooled baseline.

    Weekly samples are about a dozen items, so only a large jump can be significant: a tripwire.
    """
    from scipy.stats import fisher_exact

    if cur_n == 0 or base_n == 0:
        return {"metric": "unsupported_rate", "status": "insufficient_data", "n_current": cur_n, "n_baseline": base_n}
    p = float(fisher_exact([[cur_bad, cur_n - cur_bad], [base_bad, base_n - base_bad]])[1])
    rc, rb = cur_bad / cur_n, base_bad / base_n
    return {"metric": "unsupported_rate", "status": "drift" if p < p_threshold and rc - rb >= min_shift else "ok",
            "p_value": p, "n_current": cur_n, "n_baseline": base_n, "rate_current": rc, "rate_baseline": rb}


def evaluate_judge(current: dict, baseline_runs: list[dict], *, min_runs: int, p_threshold: float,
                   min_shift: float) -> dict:
    """Each run dict is a `judge_results` dict (`judged`, `unsupported`); runs without a verdict are skipped."""
    baseline_runs = [b for b in baseline_runs if b.get("judged")]
    if len(baseline_runs) < min_runs:
        return {"status": "insufficient_history", "baseline_runs": len(baseline_runs), "results": []}
    r = judge_unsupported_test(current.get("unsupported", 0), current.get("judged", 0),
                               sum(b.get("unsupported", 0) for b in baseline_runs),
                               sum(b.get("judged", 0) for b in baseline_runs), p_threshold, min_shift)
    return {"status": "drift" if r["status"] == "drift" else "ok", "baseline_runs": len(baseline_runs), "results": [r]}
