"""On-demand eval of agent1a's abstract-first scoring: does the best paper survive a cheap first cut?

Makes REAL Claude calls (abstract-only scoring, small) and one arXiv lookup. Never collected by pytest.

CMD:
    venv\\Scripts\\python -m evals.run_abstract_first_eval --dry-run
    venv\\Scripts\\python -m evals.run_abstract_first_eval [--repeats 2]

Inputs: the 35 papers in `evals/fixtures/papers_frozen.json`, each already scored twice on its full PDF
(`python -m evals.freeze_baseline papers`). This script scores the same papers from the abstract only,
`--repeats` times, and asks one question: when stage 1 keeps the top K by abstract score, is the paper
that full-text scoring ranks first inside that top K? If it is, stage 2 will find it and the two-stage
result equals today's.

Comparator: the same question asked of one full-text pass against the other (pass 1's top K, does it
contain pass 2's best paper, and the reverse). Haiku disagrees with itself there, so that rate, not 100%,
is the bar an abstract-first cut has to reach.
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))
os.chdir(ROOT)  # agents open prompts/... relative to the repo root
os.environ["LANGSMITH_TRACING"] = "false"

from evals import cost, results  # noqa: E402
from evals.snapshot import FIXTURES_DIR, PRIVATE_DIR  # noqa: E402
from evals.stats import rate_with_ci  # noqa: E402

FROZEN_PATH = FIXTURES_DIR / "papers_frozen.json"
ABSTRACT_CACHE = PRIVATE_DIR / "papers_abstracts_frozen.json"
KS = (3, 5, 8, 12)

_ABSTRACT_CALL_TOKENS = 2800   # rubric + tool schema + abstract (measured per call after the first run)
_OUT_TOKENS = 250


# ---------------------------------------------------------------------------
# Pure helpers (unit tested)
# ---------------------------------------------------------------------------

def ranking(totals: dict, upvotes: dict) -> list:
    """Paper ids best first. Same tie-break as agent1a's picker: total, then upvotes, then id."""
    return sorted(totals, key=lambda i: (-totals[i], -(upvotes.get(i) or 0), i))


def best(totals: dict, upvotes: dict):
    order = ranking(totals, upvotes)
    return order[0] if order else None


def rank_of(paper_id, order: list):
    """1-based position of `paper_id` in `order`, or None."""
    return order.index(paper_id) + 1 if paper_id in order else None


def in_top_k(paper_id, order: list, k: int) -> bool:
    r = rank_of(paper_id, order)
    return r is not None and r <= k


def pass_totals(items: list, pass_index: int) -> dict:
    """{paper_id: total} for one frozen full-text pass (claude_total + code-computed traction)."""
    out = {}
    for it in items:
        s = it["passes"][pass_index].get("scores")
        if s:
            out[it["id"]] = s["total"]
    return out


def recall_table(approx_orders: list, reference_totals: list, upvotes: dict, ks=KS) -> dict:
    """For every (approximating ranking, reference ranking) pair: is the reference's best paper in the
    approximation's top K? Returns {k: (hits, pairs)} plus the ranks themselves under 'ranks'."""
    ranks = []
    for order in approx_orders:
        for ref in reference_totals:
            ranks.append(rank_of(best(ref, upvotes), order))
    table = {k: (sum(1 for r in ranks if r is not None and r <= k), len(ranks)) for k in ks}
    return {"by_k": table, "ranks": ranks}


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def _estimate(n_papers: int, repeats: int) -> float:
    return cost.estimate_cost(n_papers * repeats * _ABSTRACT_CALL_TOKENS,
                              n_papers * repeats * _OUT_TOKENS, "claude-haiku-4-5-20251001")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--approve", action="store_true", help="only after the owner OKs an estimate over 2 USD")
    args = ap.parse_args(argv)

    frozen = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
    items = frozen["items"]
    est = _estimate(len(items), args.repeats)
    print(f"{len(items)} frozen papers; {args.repeats} abstract passes. Estimated cost ${est:.3f}")
    if args.dry_run:
        return 0
    cost.CostGuard(approved=args.approve).check(est)

    import anthropic
    import agent1a_fetch_papers as a1a
    import config
    import trending_papers

    key = os.environ.get("ANTHROPIC_1ST_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_1ST_API_KEY is not set")

    # abstracts: public arXiv metadata, fetched once and cached
    cache = json.loads(ABSTRACT_CACHE.read_text(encoding="utf-8")) if ABSTRACT_CACHE.exists() else {}
    base = {it["id"]: trending_papers.base_arxiv_id(it["id"]) for it in items}
    missing = sorted({b for i, b in base.items() if b not in cache})
    if missing:
        for p in trending_papers.fetch_arxiv_papers(missing, a1a._arxiv_client()):
            cache[trending_papers.base_arxiv_id(p["id"])] = p["abstract"]
        PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
        ABSTRACT_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    absent = [i for i, b in base.items() if b not in cache]
    if absent:
        sys.exit(f"no abstract found for {len(absent)} papers: {absent[:3]}")

    # meter every call (score_paper builds its own client through tracing.make_client)
    real = anthropic.Anthropic(api_key=key, timeout=60.0)
    usage = {"in": 0, "out": 0, "calls": 0}

    class _Msgs:
        def create(self, **kw):
            r = real.messages.create(**kw)
            u = getattr(r, "usage", None)
            usage["calls"] += 1
            usage["in"] += getattr(u, "input_tokens", 0) or 0
            usage["out"] += getattr(u, "output_tokens", 0) or 0
            return r

    class _Client:
        messages = _Msgs()

    a1a.tracing.make_client = lambda *a, **k: _Client()

    abstract_passes = []
    for r in range(args.repeats):
        totals, failed = {}, 0
        for it in items:
            paper = {"id": it["id"], "title": it["title"], "abstract": cache[base[it["id"]]],
                     "upvotes": it.get("upvotes"), "community_traction": it.get("community_traction") or 0}
            try:
                totals[it["id"]] = a1a.score_paper(paper, paper["abstract"])["total"]
            except Exception as e:
                failed += 1
                print(f"  [error] {it['id']}: {e}", flush=True)
        abstract_passes.append(totals)
        print(f"  abstract pass {r + 1}/{args.repeats} done ({failed} failed)", flush=True)

    upvotes = {it["id"]: it.get("upvotes") for it in items}
    full = [pass_totals(items, 0), pass_totals(items, 1)]
    abs_orders = [ranking(t, upvotes) for t in abstract_passes]
    full_orders = [ranking(t, upvotes) for t in full]

    # abstract ranking vs full-text reference, and full-text pass vs the other full-text pass
    vs_abstract = recall_table(abs_orders, full, upvotes)
    noise = {"by_k": {}, "ranks": []}
    pairs = [(full_orders[0], full[1]), (full_orders[1], full[0])]
    for order, ref in pairs:
        noise["ranks"].append(rank_of(best(ref, upvotes), order))
    for k in KS:
        noise["by_k"][k] = (sum(1 for r in noise["ranks"] if r is not None and r <= k), len(noise["ranks"]))

    metrics = {}
    for k in KS:
        h, n = vs_abstract["by_k"][k]
        metrics[f"best_paper_in_abstract_top{k}"] = rate_with_ci(h, n)
        h2, n2 = noise["by_k"][k]
        metrics[f"best_paper_in_other_fulltext_pass_top{k}"] = rate_with_ci(h2, n2)

    per_call_in = usage["in"] / max(usage["calls"], 1)
    per_call_out = usage["out"] / max(usage["calls"], 1)
    full_calls = [p for it in items for p in it["passes"]]
    full_in = sum(p["input_tokens"] for p in full_calls) / len(full_calls)
    full_out = sum(p["output_tokens"] for p in full_calls) / len(full_calls)
    n = len(items)
    cost_full = cost.estimate_cost(n * full_in, n * full_out, config.SCORING_MODEL)
    for k in KS:
        c = cost.estimate_cost(n * per_call_in + k * full_in, n * per_call_out + k * full_out, config.SCORING_MODEL)
        metrics[f"cost_per_run_abstract_first_top{k}_usd"] = {"value": round(c, 4), "n": n, "ci_low": None, "ci_high": None}
    metrics["cost_per_run_full_usd"] = {"value": round(cost_full, 4), "n": n, "ci_low": None, "ci_high": None}

    actual = cost.estimate_cost(usage["in"], usage["out"], config.SCORING_MODEL)
    doc = results.build_results(
        f"abstract_first_eval_{date.today().isoformat()}", config.SCORING_MODEL, metrics, cost_usd=round(actual, 4),
        notes=("35 frozen papers, 2 full-text passes as reference, abstract-only passes scored here. Comparator is "
               "one full-text pass against the other. Few pairs: read the ranks, not only the rates."))
    path = results.write_results(doc)
    rows = {"abstract_pass_totals": abstract_passes, "full_pass_totals": full,
            "rank_of_full_text_best_in_abstract_ranking": vs_abstract["ranks"],
            "rank_of_full_text_best_in_other_full_text_pass": noise["ranks"],
            "abstract_vs_full_spearman": None}
    from evals.freeze_baseline import spearman
    ids = sorted(set(abstract_passes[0]) & set(full[0]))
    rows["abstract_vs_full_spearman"] = spearman([abstract_passes[0][i] for i in ids], [full[0][i] for i in ids])
    path.with_name(path.stem + "_rows.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

    print(f"ranks of the full-text best paper in the abstract ranking: {vs_abstract['ranks']}")
    print(f"ranks of the full-text best paper in the other full-text pass: {noise['ranks']}")
    for k in KS:
        a, c = metrics[f"best_paper_in_abstract_top{k}"], metrics[f"best_paper_in_other_fulltext_pass_top{k}"]
        print(f"  top {k:>2}: abstract {a['value']:.2f} (n={a['n']})   full-text vs full-text {c['value']:.2f} (n={c['n']})"
              f"   cost/run ${metrics[f'cost_per_run_abstract_first_top{k}_usd']['value']:.2f}")
    print(f"  full-text everything: ${cost_full:.2f}/run;  abstract vs full Spearman: {rows['abstract_vs_full_spearman']}")
    print(f"wrote {path.relative_to(ROOT)}; actual eval cost ${actual:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
