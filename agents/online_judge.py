# agents/online_judge.py
#
# Weekly faithfulness scoring of the summaries readers actually received. Runs inside the
# healthcheck (in its own try/except there): samples a bounded set of shipped summaries, loads the
# exact source text agents 2a/2b persisted (agents/summary_sources.py), asks the judge
# (agents/judge.py) and stores counts and per-item verdicts on the run doc as `judge_results`.
# No article text or subscriber data is written back. Hard caps: JUDGE_MAX_ITEMS items and a
# JUDGE_MAX_USD estimate checked before any call is made.

import os
import random
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import judge
import pricing
import summary_sources

# Rough input tokens per word for the judge's tokenizer (the newer tokenizer gives ~30% more tokens
# than Haiku's ~1.3 per English word), plus fixed prompt/tool overhead and a generous output allowance.
_TOKENS_PER_WORD = 1.7
_OVERHEAD_IN = 900
_EXPECTED_OUT = 200


def _shipped_items(doc: dict) -> tuple[list[dict], list[dict]]:
    papers = [{"kind": "paper", "ident": p.get("id"), "title": p.get("title", ""), "summary": p.get("summary"),
               "used_fallback": bool(p.get("used_fallback"))}
              for p in (doc.get("paper_summaries") or []) if p.get("summary") and p.get("id")]
    news = []
    for arts in (doc.get("news_summaries") or {}).values():
        for a in arts:
            if a.get("summary") and a.get("url"):
                news.append({"kind": "news", "ident": a["url"], "title": a.get("title", ""), "summary": a["summary"],
                             "used_fallback": bool(a.get("used_fallback"))})
    return papers, news


def select_items(doc: dict, run_id: str, max_items: int) -> list[dict]:
    """All papers first, then a seeded news sample stratified by used_fallback (proportional, deterministic)."""
    papers, news = _shipped_items(doc)
    picked = papers[:max_items]
    room = max_items - len(picked)
    if room <= 0 or not news:
        return picked
    rng = random.Random(run_id)
    full = sorted((n for n in news if not n["used_fallback"]), key=lambda n: n["ident"])
    fall = sorted((n for n in news if n["used_fallback"]), key=lambda n: n["ident"])
    rng.shuffle(full)
    rng.shuffle(fall)
    take = min(room, len(news))
    n_fall = min(len(fall), round(take * len(fall) / len(news)))
    if fall and n_fall == 0:
        n_fall = 1
    n_full = min(len(full), take - n_fall)
    n_fall = min(len(fall), take - n_full)
    return picked + full[:n_full] + fall[:n_fall]


def estimate_item_cost(source_text: str, summary: str) -> float:
    words = min(len((source_text or "").split()), config.JUDGE_MAX_SOURCE_WORDS)
    tokens_in = int(words * _TOKENS_PER_WORD) + _OVERHEAD_IN + int(len((summary or "").split()) * _TOKENS_PER_WORD)
    return pricing.estimate_cost(tokens_in, _EXPECTED_OUT, model=config.JUDGE_MODEL)


def wilson(k: int, n: int):
    if n == 0:
        return (0.0, 1.0)
    from scipy.stats import binomtest
    ci = binomtest(k, n).proportion_ci(confidence_level=0.95, method="wilson")
    return (ci.low, ci.high)


def judge_run(db, client, run_id: str, doc: dict, max_items=None, max_usd=None) -> dict:
    """Judge a bounded sample for this run and store `judge_results` on its doc. Idempotent: a run that
    already has results is returned as is, so a re-run of the healthcheck never spends twice."""
    existing = doc.get("judge_results")
    if isinstance(existing, dict) and existing.get("items") is not None:
        return existing
    max_items = config.JUDGE_MAX_ITEMS if max_items is None else max_items
    max_usd = config.JUDGE_MAX_USD if max_usd is None else max_usd

    template = judge.load_prompt()
    rows, est_total = [], 0.0
    for it in select_items(doc, run_id, max_items):
        src = summary_sources.load_source(db, run_id, it["kind"], it["ident"])
        if not src or not src.get("text"):
            rows.append({"kind": it["kind"], "ident": it["ident"], "used_fallback": it["used_fallback"], "status": "no_source"})
            continue
        it["source"] = src["text"]
        est_total += estimate_item_cost(src["text"], it["summary"])
        rows.append(it)

    result = {"model": config.JUDGE_MODEL, "estimated_usd": round(est_total, 4), "cap_usd": max_usd}
    if est_total > max_usd:
        result.update(items=[], skipped="cost_cap", n=0, judged=0, unsupported=0, errors=0, no_source=0, cost_usd=0.0)
        _store(db, run_id, result)
        return result

    items, cost, in_tok, out_tok = [], 0.0, 0, 0
    for it in rows:
        if it.get("status") == "no_source":
            items.append(it)
            continue
        base = {"kind": it["kind"], "ident": it["ident"], "used_fallback": it["used_fallback"]}
        try:
            v = judge.judge_summary(client, it["title"], it["source"], it["summary"], template=template)
            in_tok += v["input_tokens"]
            out_tok += v["output_tokens"]
            cost += pricing.estimate_cost(v["input_tokens"], v["output_tokens"], model=config.JUDGE_MODEL)
            items.append({**base, "status": "judged", "supported": v["supported"],
                          "n_unsupported_claims": len(v["unsupported_claims"])})
        except Exception as e:  # one bad call must not lose the rest
            items.append({**base, "status": "error", "error": type(e).__name__})
    judged = [i for i in items if i["status"] == "judged"]
    unsupported = sum(1 for i in judged if not i["supported"])
    lo, hi = wilson(unsupported, len(judged))
    result.update(items=items, n=len(items), judged=len(judged), unsupported=unsupported,
                  errors=sum(1 for i in items if i["status"] == "error"),
                  no_source=sum(1 for i in items if i["status"] == "no_source"),
                  unsupported_ci_low=lo, unsupported_ci_high=hi,
                  input_tokens=in_tok, output_tokens=out_tok, cost_usd=round(cost, 4))
    _store(db, run_id, result)
    return result


def _store(db, run_id: str, result: dict) -> None:
    db.collection(config.FIRESTORE_COLLECTION).document(run_id).set({"judge_results": result}, merge=True)
