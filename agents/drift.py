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
