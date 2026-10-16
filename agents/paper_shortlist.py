# agents/paper_shortlist.py
#
# Optional agent1a mode (AGENT1A_MODE=abstract_first): score all papers from their abstract (cheap,
# no PDF download), then score the full PDF of only the top K. Cost roadmap Step 3.
#
#   full            (default) download every PDF and score it: 35 calls of ~12k tokens.
#   abstract_first  35 calls of ~2k tokens, then K calls of ~12k tokens.
#
# The paper that wins is always one that was scored on its full text: papers that were only scored
# from their abstract rank below every second-pass paper, so an abstract-only score (a different,
# noisier scale) can never beat a full-text score. These helpers are pure; agent1a does the I/O.

import os

MODE_FULL = "full"
MODE_ABSTRACT_FIRST = "abstract_first"

DEFAULT_TOP_K = 8

STAGE_ABSTRACT = 1   # scored from the abstract only
STAGE_SECOND = 2     # second pass: full PDF text (or the abstract again if the PDF could not be read)


def agent1a_mode() -> str:
    """AGENT1A_MODE, read on every run (no redeploy to switch). Anything unknown means 'full'."""
    mode = (os.environ.get("AGENT1A_MODE") or MODE_FULL).strip().lower()
    return MODE_ABSTRACT_FIRST if mode == MODE_ABSTRACT_FIRST else MODE_FULL


def top_k() -> int:
    try:
        return max(1, int(os.environ.get("AGENT1A_FULLTEXT_TOP_K", DEFAULT_TOP_K)))
    except (TypeError, ValueError):
        return DEFAULT_TOP_K


def _total(result: dict) -> int:
    return (result.get("scores") or {}).get("total", -1)


def pick_for_second_pass(stage1: list, k: int) -> list[int]:
    """Indices of the papers to score on full text: every paper whose abstract pass failed (it has
    no score to rank by, and a failure must not drop a paper), plus the top `k` by abstract total.
    Ties go to the more upvoted paper, then to input order. Stable and sorted."""
    failed = [i for i, r in enumerate(stage1) if r.get("scores") is None]
    ok = [i for i, r in enumerate(stage1) if r.get("scores") is not None]
    ok.sort(key=lambda i: (-_total(stage1[i]), -(stage1[i].get("upvotes") or 0), i))
    return sorted(set(failed) | set(ok[:k]))


def merge_stages(stage1: list, picked: list[int], stage2: list) -> list:
    """Final scored list, best first. `stage2[j]` is the second-pass result of paper `picked[j]`.

    A second-pass score replaces the abstract score. If the second pass failed the paper keeps its
    abstract result. Second-pass papers rank above abstract-only ones, then by total."""
    second = {i: r for i, r in zip(picked, stage2)}
    merged = []
    for i, r1 in enumerate(stage1):
        r2 = second.get(i)
        if r2 is not None and r2.get("scores") is not None:
            merged.append({**r2, "scoring_stage": STAGE_SECOND})
        else:
            merged.append({**r1, "scoring_stage": STAGE_ABSTRACT})
    merged.sort(key=lambda r: (r.get("scores") is not None, r["scoring_stage"] == STAGE_SECOND, _total(r)),
                reverse=True)
    return merged


def summary(n_papers: int, picked: list[int], merged: list, k: int) -> dict:
    return {
        "mode": MODE_ABSTRACT_FIRST,
        "papers": n_papers,
        "top_k": k,
        "second_pass": len(picked),
        "second_pass_scored": sum(1 for r in merged if r.get("scoring_stage") == STAGE_SECOND),
        "abstract_only": sum(1 for r in merged if r.get("scoring_stage") == STAGE_ABSTRACT),
    }
