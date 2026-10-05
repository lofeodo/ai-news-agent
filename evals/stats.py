"""Statistics helpers shared by every eval (thin wrappers over scipy / scikit-learn)."""
from scipy.stats import binomtest
from sklearn.metrics import cohen_kappa_score


def wilson_interval(k, n, confidence=0.95):
    """Wilson score interval for k successes out of n. Returns (low, high); n == 0 gives (0.0, 1.0)."""
    if n == 0:
        return (0.0, 1.0)
    ci = binomtest(k, n).proportion_ci(confidence_level=confidence, method="wilson")
    return (ci.low, ci.high)


def rate_with_ci(k, n, confidence=0.95):
    """A rate reported the way the roadmap requires: value, n and a Wilson interval."""
    low, high = wilson_interval(k, n, confidence)
    return {"value": (k / n) if n else None, "n": n, "ci_low": low, "ci_high": high}


def cohens_kappa(a, b):
    """Chance-corrected agreement between two equal-length label sequences."""
    if len(a) != len(b):
        raise ValueError(f"label lists differ in length: {len(a)} vs {len(b)}")
    if not a:
        raise ValueError("cannot compute kappa on empty label lists")
    return float(cohen_kappa_score(a, b))


def paired_wins_losses(a_correct, b_correct):
    """Per-item comparison of variant A vs B (booleans per item, same order).

    wins = B right and A wrong; losses = B wrong and A right; ties = same outcome.
    """
    if len(a_correct) != len(b_correct):
        raise ValueError(f"lists differ in length: {len(a_correct)} vs {len(b_correct)}")
    wins = sum(1 for a, b in zip(a_correct, b_correct) if b and not a)
    losses = sum(1 for a, b in zip(a_correct, b_correct) if a and not b)
    return {"wins": wins, "losses": losses, "ties": len(a_correct) - wins - losses, "n": len(a_correct)}
