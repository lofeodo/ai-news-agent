"""On-demand prompt-injection eval: poisoned vs clean inputs through the real agent code paths.

Makes REAL Claude calls. Never collected by pytest.

CMD:
    venv\\Scripts\\python -m evals.run_injection_eval --dry-run          (counts + cost estimate, no API calls)
    venv\\Scripts\\python -m evals.run_injection_eval --name injection_eval_baseline
    venv\\Scripts\\python -m evals.run_injection_eval --approve          (only after the owner OKs an estimate over 2 USD)

Every case runs `--repeats` times with the injection (attack arm) and without it (control arm), through
the same functions production uses (agents 1b categorize and review loop, 2a, 2b, 3 selection and intro,
and the subscriptions refine endpoint). Success is judged deterministically (see injection_eval.py), so
there is no LLM judge. What this can and cannot show: it detects canary-style compliance (a forced
category, a canary string, a leaked guard sentence, a fetch of a planted URL, a markup payload echoed
by the model). It does not detect subtle steering. Rates come with n and Wilson intervals; n is small.
"""
import argparse
import copy
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))
os.chdir(ROOT)  # agents open prompts/... relative to the repo root

import agent1b_fetch_news as a1b  # noqa: E402
import agent2a_summarize_papers as a2a  # noqa: E402
import agent2b_summarize_news as a2b  # noqa: E402
import agent3_compose as a3  # noqa: E402
import config  # noqa: E402
from agent1b_graph import MeteredClient, ReviewConfig, _Meter, build_review_graph  # noqa: E402
from evals import cost, injection_eval, results  # noqa: E402

# Rough per-trial token budgets, used only for the pre-run estimate (actual usage is metered).
_EST_TOKENS = {"categorize": (2500, 150), "review": (7000, 400), "summarize_2a": (1500, 400),
               "summarize_2b": (1500, 300), "select_3": (1500, 60), "intro_3": (1200, 250), "refine": (150, 30)}

_FILLERS = [
    {"title": "Study finds remote work did not hurt productivity", "description": "Researchers surveyed 2,000 employees."},
    {"title": "New privacy law takes effect next month", "description": "Companies must update consent flows."},
    {"title": "Museum opens exhibit on early computing", "description": "The exhibit features restored machines."},
]


_REFINE_LOCK = threading.Lock()  # mock.patch swaps module globals, so refine trials must not overlap


class Tap:
    """Records everything the model produced (text blocks plus tool-call inputs) for the judge."""

    def __init__(self, client):
        self.messages = self
        self._inner, self.chunks, self._lock = client, [], threading.Lock()

    def create(self, **kw):
        resp = self._inner.messages.create(**kw)
        parts = []
        for b in getattr(resp, "content", None) or []:
            if getattr(b, "type", "") == "text":
                parts.append(b.text)
            elif getattr(b, "type", "") == "tool_use":
                parts.append(json.dumps(b.input))
        with self._lock:
            self.chunks.extend(parts)
        return resp

    @property
    def output(self):
        return "\n".join(self.chunks)


class StubFetcher:
    """Serves one canned page for any URL (so a planted URL is 'reachable') and records every request."""

    def __init__(self, text):
        self.text, self.urls = text, []

    def __call__(self, url, timeout):
        self.urls.append(url)
        return self.text, None


def _prompt(name):
    return Path(f"prompts/{name}").read_text(encoding="utf-8")


def _mk_article(target, injection, field):
    art = {"source": "newsapi", "title": target["title"], "description": target["description"],
           "url": target["url"], "language": "en", "hn_score": None}
    if field in art:  # field may instead be the fetched page text, applied by run_review
        art[field] = injection_eval.with_injection(art[field], injection)
    return art


def run_categorize(client, target, field, injection):
    art = _mk_article(target, injection, field)
    batch = [art] + [{"source": "newsapi", "url": f"https://example.com/filler{i}", "language": "en",
                      "hn_score": None, **f} for i, f in enumerate(_FILLERS)]
    out = a1b.filter_batch(batch, 0, _prompt("news_filter_prompt.txt"), client)
    cat = next((a["category"] for a in out if a["url"] == art["url"]), None)
    return {"category": cat}


def run_review(client, target, field, injection):
    art = _mk_article(target, injection, field)
    art.update(category=target["expected"], confidence=2)
    fetched = injection_eval.with_injection(target["fetched_text"], injection if field == "fetched_text" else "")
    fetcher = StubFetcher(fetched)
    cfg = ReviewConfig(confidence_threshold=4, max_articles=30, max_iterations=3, fetch_timeout=10)
    state = build_review_graph(client, fetcher, cfg).invoke({"article": art, "messages": [], "iterations": 0})
    return {"category": state["reviewed"][0]["final_category"], "fetched": fetcher.urls}


def run_summarize_2a(client, target, field, injection):
    paper = {"title": target["title"], "text": target["text"]}
    paper[field] = injection_eval.with_injection(paper[field], injection)
    a2a.summarize_paper({"title": paper["title"]}, paper["text"], _prompt("paper_summary_prompt.txt"), client)
    return {}


def run_summarize_2b(client, target, field, injection):
    art = {"title": target["title"], "description": "", "language": "en"}
    text = target["text"]
    if field == "title":
        art["title"] = injection_eval.with_injection(art["title"], injection)
    else:
        text = injection_eval.with_injection(text, injection)
    a2b.summarize_article(art, text, _prompt("news_summary_prompt.txt"), _prompt("news_summary_fallback_prompt.txt"),
                          "", client)
    return {}


def run_select_3(client, target, field, injection):
    arts = copy.deepcopy(target["articles"])
    poisoned = arts[target["poison_index"]]
    poisoned[field] = injection_eval.with_injection(poisoned[field], injection)
    a3.select_articles_for_category(target["category"], arts, _prompt("article_selection_prompt.txt"), client)
    return {}


def run_intro_3(client, target, field, injection):
    headlines = list(target["headlines"])
    headlines[0] = injection_eval.with_injection(headlines[0], injection)
    papers = [{"title": t, "scores": {"total": 20}} for t in target["papers"]]
    selected = {"Industry & Business": [{"title": h, "hn_score": None} for h in headlines]}
    a3.write_intro(papers, selected, _prompt("intro_prompt.txt"), client)
    return {}


def run_refine(client, target, field, injection):
    """Drive the real endpoint through FastAPI's TestClient with auth, tier and the Anthropic client stubbed in."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import agent_subscriptions as subs
    app = FastAPI()
    app.state.limiter = subs.limiter
    app.include_router(subs.router)
    app.dependency_overrides[subs.get_current_user] = lambda: {"email": "eval@example.com", "uid": "eval"}
    raw = injection_eval.with_injection(target["raw_topic"], injection)
    with mock.patch.object(subs.limiter, "enabled", False), \
            mock.patch.object(subs, "_get_user_tier", lambda email: "premium"), \
            mock.patch.object(subs, "_get_anthropic_api_key", lambda: "unused"), \
            mock.patch.object(subs.anthropic, "Anthropic", lambda **kw: client):
        TestClient(app).post("/auth/sections/refine", json={"raw_topic": raw})
    return {}


RUNNERS = {"categorize": run_categorize, "review": run_review, "summarize_2a": run_summarize_2a,
           "summarize_2b": run_summarize_2b, "select_3": run_select_3, "intro_3": run_intro_3,
           "refine": run_refine}


def run_trial(case, arm, client):
    """One model interaction; returns the judged trial row. Errors count as 'no success' but are recorded."""
    tap = Tap(client)
    injection = case["injection"] if arm == "attack" else ""
    try:
        outcome = RUNNERS[case["agent"]](tap, case["target"], case["field"], injection)
        error = None
    except Exception as e:  # a crash is not an attack success; keep it visible in the rows
        outcome, error = {}, f"{type(e).__name__}: {e}"
    outcome = {"output": tap.output, "category": None, "fetched": [], **outcome}
    return {"case": case["id"], "agent": case["agent"], "attack": case["attack"], "arm": arm,
            "success": injection_eval.judge(case["success"], outcome), "error": error,
            "category": outcome["category"], "fetched": outcome["fetched"], "output": outcome["output"][:600]}


def run_eval(cases, client, repeats, workers=5):
    """Returns (trials, usage). `client` is any object with .messages.create (real or stub)."""
    meter = _Meter()
    metered = MeteredClient(client, meter)
    jobs = [(c, arm) for c in cases for arm in ("attack", "control") for _ in range(repeats)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        trials = list(pool.map(lambda j: run_trial(j[0], j[1], metered), jobs))
    return trials, meter.as_usage()


def estimate_tokens(cases, repeats):
    tin = tout = 0
    for c in cases:
        i, o = _EST_TOKENS[c["agent"]]
        tin += 2 * repeats * i
        tout += 2 * repeats * o
    return tin, tout


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print counts and the cost estimate, call nothing")
    ap.add_argument("--approve", action="store_true", help="owner approved an estimate above the 2 USD limit")
    ap.add_argument("--repeats", type=int, default=5, help="trials per case per arm (default 5)")
    ap.add_argument("--name", default=f"injection_eval_{date.today().isoformat()}")
    args = ap.parse_args(argv)

    cases = injection_eval.load_cases()
    est_in, est_out = estimate_tokens(cases, args.repeats)
    est = cost.estimate_cost(est_in, est_out)
    print(f"cases: {len(cases)}; repeats: {args.repeats}; trials: {len(cases) * 2 * args.repeats} (attack + control)")
    print(f"estimated worst-case cost: ${est:.3f} ({est_in:,} in / {est_out:,} out tokens, model {config.SCORING_MODEL})")
    if args.dry_run:
        return 0
    cost.CostGuard(approved=args.approve).check(est)

    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_1ST_API_KEY"))
    trials, usage = run_eval(cases, client, args.repeats)

    metrics = injection_eval.score(trials)
    actual = cost.estimate_cost(usage["input"], usage["output"])
    errors = sum(1 for t in trials if t["error"])
    notes = (f"{len(cases)} hand-written cases x {args.repeats} repeats x 2 arms; control arm omits the injection. "
             f"Success is judged deterministically (canary string, forced category, leaked guard sentence, planted "
             f"fetch URL, markup echoed by the model): it misses subtle steering. n per rate is small. "
             f"{errors} trials raised errors and count as no success.")
    doc = results.build_results(args.name, config.SCORING_MODEL, metrics, cost_usd=round(actual, 4), notes=notes)
    path = results.write_results(doc)
    path.with_name(f"{args.name}_trials.json").write_text(json.dumps(trials, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path} and {args.name}_trials.json; cost ${actual:.3f}; {errors} errored trials")
    for line in injection_eval.summary_lines(metrics):
        print(" ", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
