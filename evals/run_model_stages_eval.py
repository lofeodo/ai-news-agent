"""Model comparison (Haiku vs DeepSeek) for agent1b categorize, agent2b summaries and agent1a paper scoring.
Makes REAL API calls. Never collected by pytest.

CMD (needs ANTHROPIC_1ST_API_KEY and DEEPSEEK_API_KEY):
    venv\\Scripts\\python -m evals.run_model_stages_eval categorize --dry-run
    venv\\Scripts\\python -m evals.run_model_stages_eval categorize
    venv\\Scripts\\python -m evals.run_model_stages_eval summaries
    venv\\Scripts\\python -m evals.run_model_stages_eval papers

All inputs are the frozen sets from Step 1 (evals/fixtures/ and the gitignored evals/fixtures/private/), run through
the production functions with MODEL_<STAGE> pointing at the arm's model, so the seam in agents/llm.py is under test too.
  categorize  the 500 frozen articles; accuracy against the owner's 100 gold labels (Haiku once, DeepSeek twice)
  summaries   the 60 frozen articles; success/length/cost, plus a pairwise Sonnet comparison (both orders) that also
              flags claims the source does not support. The judge is NOT validated (kappa 0.04 on absolute grading):
              read it as a smoke test and settle by hand with the side-by-side rows file.
  papers      the 35 frozen papers, full text; rank agreement with Haiku's two frozen passes against Haiku's own
              noise (Spearman 0.77, 5 of top 8), and whether Haiku's top 3 stay inside DeepSeek's top 5
"""
import argparse
import json
import os
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from evals import run_lang_filter_eval as base  # sets up sys.path, cwd, tracing off
from evals import run_review_eval as rev

import agent1a_fetch_papers as a1a  # noqa: E402
import agent1b_fetch_news as a1b  # noqa: E402
import agent2b_summarize_news as a2b  # noqa: E402
import config  # noqa: E402
import llm  # noqa: E402
import pricing  # noqa: E402
from evals import cost, results, review_eval  # noqa: E402
from evals.freeze_baseline import spearman, top_k_overlap, word_count  # noqa: E402
from evals.snapshot import FIXTURES_DIR, PRIVATE_DIR  # noqa: E402
from evals.stats import rate_with_ci, wilson_interval  # noqa: E402

ROOT = base.ROOT
DS = "deepseek-flash"
HAIKU = config.SCORING_MODEL


def _clients():
    import anthropic
    claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_1ST_API_KEY"], timeout=90.0)
    ds = anthropic.Anthropic(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=llm.DEEPSEEK_BASE_URL, timeout=90.0)
    return claude, llm.RoutedClient(claude, ds)


def _arm_env(var, model):
    if model == DS:
        os.environ[var] = DS
    else:
        os.environ.pop(var, None)


def _m(value, n):
    return {"value": value, "n": n, "ci_low": None, "ci_high": None}


def _write(name, model_label, metrics, usd, notes, rows):
    doc = results.build_results(name, model_label, metrics, cost_usd=round(usd, 4), notes=notes)
    path = results.write_results(doc)
    rows_path = PRIVATE_DIR / f"{name}_rows.json"
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    rows_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for k, v in metrics.items():
        ci = f" [{v['ci_low']:.3f}, {v['ci_high']:.3f}]" if v.get("ci_low") is not None else ""
        print(f"  {k}: {v['value']}{ci}")
    print(f"wrote {path.relative_to(ROOT)} and {rows_path.relative_to(ROOT)}; eval cost ${usd:.2f}")


# ---------------------------------------------------------------------------
# categorize
# ---------------------------------------------------------------------------

def cmd_categorize(args) -> int:
    articles = rev.load_frozen()
    gold = rev.reconcile_gold(review_eval.load_gold(rev.GOLD_PATH), articles)
    ids = {a["_id"] for a in articles}
    gold = {i: g for i, g in gold.items() if i in ids}
    est = cost.estimate_cost(*rev.estimate_tokens(articles, 0), HAIKU) * 0.6
    print(f"{len(articles)} frozen articles, {len(gold)} gold labels. Estimated cost ${est:.2f}")
    if args.dry_run:
        return 0
    cost.CostGuard(approved=False).check(est)
    _, client = _clients()

    arms = (("haiku", HAIKU), ("ds_a", DS), ("ds_b", DS))
    preds, meters = {}, {}
    for name, model in arms:
        _arm_env("MODEL_CATEGORIZE", model)
        meter = base.Meter()
        out = a1b.filter_and_categorize(articles, client=base.Metered(client, meter), with_confidence=False)
        preds[name] = {a["_id"]: a["category"] for a in out}
        meters[name] = (meter, model)
        print(f"{name}: {meter.calls} calls, {meter.input} in / {meter.output} out, {len(out)} categorized, "
              f"{llm.fallbacks} fallbacks so far", flush=True)

    metrics, rows = {}, []
    for name, _ in arms:
        k = sum(1 for i, g in gold.items() if preds[name].get(i) == g["gold"])
        metrics[f"accuracy_{name}"] = rate_with_ci(k, len(gold))
        metrics[f"coverage_{name}"] = rate_with_ci(sum(1 for i in gold if i in preds[name]), len(gold))
    both = [i for i in gold if i in preds["haiku"] and i in preds["ds_a"]]
    metrics["agreement_ds_a_vs_haiku"] = rate_with_ci(sum(preds["ds_a"][i] == preds["haiku"][i] for i in both), len(both))
    both = [i for i in gold if i in preds["ds_a"] and i in preds["ds_b"]]
    metrics["agreement_ds_b_vs_ds_a"] = rate_with_ci(sum(preds["ds_a"][i] == preds["ds_b"][i] for i in both), len(both))
    total = 0.0
    for name, (m, model) in meters.items():
        usd = pricing.estimate_cost(m.input, m.output, model)
        total += usd
        metrics[f"cost_usd_{name}"] = _m(round(usd, 4), len(articles))
        metrics[f"tokens_{name}"] = _m([m.input, m.output], len(articles))
    metrics["fallbacks_to_claude"] = _m(llm.fallbacks, len(articles))
    for i, g in gold.items():
        rows.append({"id": i, "gold": g["gold"], **{n: preds[n].get(i) for n, _ in arms}})
    _write(f"categorize_model_eval_{date.today().isoformat()}", f"{HAIKU} vs {DS}", metrics, total,
           "Frozen 500 articles (300-char snippets, same as the review eval); owner's 100 gold labels. DeepSeek priced "
           "off-peak. Gold labels may be anchored by the first-pass category shown to the labeler.", rows)
    return 0


# ---------------------------------------------------------------------------
# summaries
# ---------------------------------------------------------------------------

JUDGE_TOOL = {
    "name": "compare_summaries",
    "description": "Compare two summaries of the same article against the source text.",
    "input_schema": {"type": "object", "properties": {
        "better": {"type": "string", "enum": ["A", "B", "tie"],
                   "description": "Which summary is more accurate, clear and useful to a newsletter reader."},
        "a_unsupported_claim": {"type": "boolean", "description": "A states something the source does not support."},
        "b_unsupported_claim": {"type": "boolean", "description": "B states something the source does not support."},
    }, "required": ["better", "a_unsupported_claim", "b_unsupported_claim"]},
}
JUDGE_PROMPT = ("You compare two short newsletter summaries of one article. Judge only against the source text: "
                "factual accuracy first, then clarity and usefulness to a reader. Ignore length unless it hurts.\n\n"
                "<source>\n{source}\n</source>\n\n<summary_A>\n{a}\n</summary_A>\n\n<summary_B>\n{b}\n</summary_B>")


def _judge(claude, source, a, b):
    r = claude.messages.create(model=config.JUDGE_MODEL, max_tokens=300, tools=[JUDGE_TOOL],
                               tool_choice={"type": "auto"},
                               messages=[{"role": "user", "content": JUDGE_PROMPT.format(source=source[:6000], a=a, b=b)}])
    block = next((b_ for b_ in r.content if b_.type == "tool_use"), None)
    return (block.input if block else None), r.usage


def cmd_summaries(args) -> int:
    frozen = json.loads((FIXTURES_DIR / "summaries_frozen.json").read_text(encoding="utf-8"))
    sources = json.loads((PRIVATE_DIR / "summary_sources_frozen.json").read_text(encoding="utf-8"))
    items = [i for i in frozen["items"] if i["url"] in sources]
    est = 60 * (1_300 * 1 + 80 * 5) / 1e6 + len(items) * 2 * (2_200 * 2 + 100 * 10) / 1e6
    print(f"{len(items)} frozen articles. Estimated cost ${est:.2f}")
    if args.dry_run:
        return 0
    cost.CostGuard(approved=False).check(est)
    claude, client = _clients()
    templates = {n: Path("prompts", f"{n}.txt").read_text(encoding="utf-8")
                 for n in ("news_summary_prompt", "news_summary_fallback_prompt", "quebec_french_style")}

    def run_arm(model):
        _arm_env("MODEL_NEWS_SUMMARY", model)
        meter, lock, out = base.Meter(), threading.Lock(), {}

        def one(it):
            src = sources[it["url"]]
            fb = bool(src["used_fallback"])
            art = {"title": it["title"], "url": it["url"], "language": it["language"], "category": it["category"],
                   "hn_score": it["hn_score"], "description": src["text"] if fb else ""}
            res = a2b.summarize_article(art, None if fb else src["text"], templates["news_summary_prompt"],
                                        templates["news_summary_fallback_prompt"], templates["quebec_french_style"],
                                        base.Metered(client, meter))
            with lock:
                out[it["url"]] = res
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(one, items))
        return out, meter

    res_h, m_h = run_arm(HAIKU)
    res_d, m_d = run_arm(DS)
    print(f"haiku: {m_h.calls} calls {m_h.input}/{m_h.output}; deepseek: {m_d.calls} calls {m_d.input}/{m_d.output}; "
          f"fallbacks {llm.fallbacks}", flush=True)

    rng = random.Random(0)
    tally = {"ds_wins": 0, "haiku_wins": 0, "ties": 0, "inconsistent": 0, "unusable": 0}
    unsupported = {"haiku": 0, "ds": 0}
    judge_in = judge_out = 0
    rows = []
    both = [i for i in items if res_h[i["url"]].get("summary") and res_d[i["url"]].get("summary")]

    def judge_item(it):
        s_h, s_d = res_h[it["url"]]["summary"], res_d[it["url"]]["summary"]
        src = sources[it["url"]]["text"]
        v1, u1 = _judge(claude, src, s_h, s_d)   # A = haiku
        v2, u2 = _judge(claude, src, s_d, s_h)   # A = deepseek
        return it, s_h, s_d, v1, v2, u1, u2

    with ThreadPoolExecutor(max_workers=4) as ex:
        judged = list(ex.map(judge_item, both))
    for it, s_h, s_d, v1, v2, u1, u2 in judged:
        judge_in += u1.input_tokens + u2.input_tokens
        judge_out += u1.output_tokens + u2.output_tokens
        if not v1 or not v2:
            tally["unusable"] += 1
            verdict = None
        else:
            w1 = {"A": "haiku", "B": "ds", "tie": "tie"}[v1["better"]]
            w2 = {"A": "ds", "B": "haiku", "tie": "tie"}[v2["better"]]
            unsupported["haiku"] += int(v1["a_unsupported_claim"]) + int(v2["b_unsupported_claim"])
            unsupported["ds"] += int(v1["b_unsupported_claim"]) + int(v2["a_unsupported_claim"])
            if w1 == w2:
                verdict = w1
                tally["ties" if w1 == "tie" else f"{'ds' if w1 == 'ds' else 'haiku'}_wins"] += 1
            else:
                verdict = "inconsistent"  # the order flipped the answer: no preference
                tally["inconsistent"] += 1
        rows.append({"url": it["url"], "title": it["title"], "haiku": s_h, "deepseek": s_d, "verdict": verdict})

    n = len(both)
    metrics = {
        "success_haiku": rate_with_ci(sum(1 for i in items if res_h[i["url"]].get("summary")), len(items)),
        "success_ds": rate_with_ci(sum(1 for i in items if res_d[i["url"]].get("summary")), len(items)),
        "mean_words_haiku": _m(round(sum(word_count(r["haiku"]) for r in rows) / max(n, 1), 1), n),
        "mean_words_ds": _m(round(sum(word_count(r["deepseek"]) for r in rows) / max(n, 1), 1), n),
        "judge_ds_preferred": rate_with_ci(tally["ds_wins"], n),
        "judge_haiku_preferred": rate_with_ci(tally["haiku_wins"], n),
        "judge_tie": rate_with_ci(tally["ties"], n),
        "judge_order_inconsistent": rate_with_ci(tally["inconsistent"], n),
        "judge_unsupported_claim_calls_haiku": _m(unsupported["haiku"], 2 * n),
        "judge_unsupported_claim_calls_ds": _m(unsupported["ds"], 2 * n),
        "fallbacks_to_claude": _m(llm.fallbacks, len(items)),
    }
    usd_h = pricing.estimate_cost(m_h.input, m_h.output, HAIKU)
    usd_d = pricing.estimate_cost(m_d.input, m_d.output, DS)
    usd_j = pricing.estimate_cost(judge_in, judge_out, config.JUDGE_MODEL)
    metrics["cost_usd_haiku"] = _m(round(usd_h, 4), len(items))
    metrics["cost_usd_ds"] = _m(round(usd_d, 4), len(items))
    _write(f"summaries_model_eval_{date.today().isoformat()}", f"{HAIKU} vs {DS}", metrics, usd_h + usd_d + usd_j,
           "60 frozen articles, production prompt. Pairwise judge = Sonnet 5.5 in both orders, unvalidated (smoke test, "
           "not a gate). 'inconsistent' = the answer flipped with the order. Read the side-by-side rows file by hand.", rows)
    return 0


# ---------------------------------------------------------------------------
# papers
# ---------------------------------------------------------------------------

def cmd_papers(args) -> int:
    frozen = json.loads((FIXTURES_DIR / "papers_frozen.json").read_text(encoding="utf-8"))
    texts = json.loads((PRIVATE_DIR / "papers_text_frozen.json").read_text(encoding="utf-8"))
    abstracts = json.loads((PRIVATE_DIR / "papers_abstracts_frozen.json").read_text(encoding="utf-8"))
    items = frozen["items"]
    est = 2 * len(items) * (12_000 * 0.15 + 250 * 0.6) / 1e6 + 0.01
    print(f"{len(items)} frozen papers, 2 DeepSeek passes. Estimated cost ${est:.2f}")
    if args.dry_run:
        return 0
    cost.CostGuard(approved=False).check(est)
    _, client = _clients()
    _arm_env("MODEL_PAPER_SCORE", DS)
    meter = base.Meter()
    a1a.llm.make_client = lambda *a, **k: base.Metered(client, meter)  # score_paper builds its own client

    def short(pid):
        import re
        return re.sub(r"v\d+$", "", pid.split("/")[-1])

    def one(it, _pass):
        paper = {"id": it["id"], "title": it["title"], "upvotes": it["upvotes"],
                 "community_traction": it["community_traction"], "abstract": abstracts.get(short(it["id"]), "")}
        try:
            return a1a.score_paper(paper, texts[it["id"]])["claude_total"]
        except Exception as e:
            print(f"  [fail] {it['title'][:40]}: {type(e).__name__}: {str(e)[:100]}")
            return None

    passes = []
    for p in range(2):
        with ThreadPoolExecutor(max_workers=4) as ex:
            res = list(ex.map(lambda it: one(it, p), items))
        passes.append({it["id"]: s for it, s in zip(items, res) if s is not None})
        print(f"deepseek pass {p + 1}: {len(passes[-1])}/{len(items)} scored", flush=True)

    def totals(k):
        return {i["id"]: i["passes"][k]["scores"]["claude_total"] for i in items if i["passes"][k]["scores"]}
    h1, h2 = totals(0), totals(1)
    h_mean = {i: (h1[i] + h2[i]) / 2 for i in h1 if i in h2}
    d1, d2 = passes
    d_mean = {i: (d1[i] + d2[i]) / 2 for i in d1 if i in d2}
    ids = sorted(set(h_mean) & set(d_mean))

    def top(d, k):
        return set(sorted(d, key=lambda i: (-d[i], i))[:k])
    metrics = {
        "scored_pass1": rate_with_ci(len(d1), len(items)), "scored_pass2": rate_with_ci(len(d2), len(items)),
        "spearman_haiku_pass1_vs_pass2": _m(spearman([h1[i] for i in ids], [h2[i] for i in ids]), len(ids)),
        "spearman_ds_pass1_vs_pass2": _m(spearman([d1[i] for i in ids if i in d1 and i in d2],
                                                   [d2[i] for i in ids if i in d1 and i in d2]), len(ids)),
        "spearman_ds_vs_haiku": _m(spearman([d_mean[i] for i in ids], [h_mean[i] for i in ids]), len(ids)),
        "top8_overlap_ds_vs_haiku": _m(top_k_overlap(h_mean, d_mean, 8), len(ids)),
        "top8_overlap_haiku_pass1_vs_pass2": _m(top_k_overlap(h1, h2, 8), len(ids)),
        "haiku_top3_inside_ds_top5": _m(len(top(h_mean, 3) & top(d_mean, 5)), 3),
        "mean_total_ds": _m(round(sum(d_mean[i] for i in ids) / len(ids), 2), len(ids)),
        "mean_total_haiku": _m(round(sum(h_mean[i] for i in ids) / len(ids), 2), len(ids)),
        "fallbacks_to_claude": _m(llm.fallbacks, len(items) * 2),
    }
    usd = pricing.estimate_cost(meter.input, meter.output, DS)
    metrics["cost_usd_ds_two_passes"] = _m(round(usd, 4), len(items) * 2)
    metrics["tokens_ds_two_passes"] = _m([meter.input, meter.output], len(items) * 2)
    rows = [{"id": i, "title": next(x["title"] for x in items if x["id"] == i), "haiku": h_mean[i], "deepseek": d_mean[i]}
            for i in ids]
    _write(f"papers_model_eval_{date.today().isoformat()}", f"{HAIKU} vs {DS}", metrics, usd,
           "35 frozen papers, full text, DeepSeek twice vs Haiku's two frozen passes (Haiku's own noise: the floor). "
           "Reference is one noisy model's opinion, not the owner's pick.", rows)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=("categorize", "summaries", "papers"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    return {"categorize": cmd_categorize, "summaries": cmd_summaries, "papers": cmd_papers}[args.stage](args)


if __name__ == "__main__":
    sys.exit(main())
