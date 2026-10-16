"""On-demand eval of agent1b's language filter modes. Makes REAL Claude calls. Never collected by pytest.

CMD:
    venv\\Scripts\\python -m evals.run_lang_filter_eval --dry-run
    venv\\Scripts\\python -m evals.run_lang_filter_eval            (refuses if the estimate exceeds 2 USD)
    venv\\Scripts\\python -m evals.run_lang_filter_eval --approve   (only after the owner OKs the estimate)

Pool: a fresh, unfiltered agent1b fetch (Hacker News + NewsAPI) after the code prefilter, i.e. exactly what
the language filter receives in production. Cached under `evals/fixtures/private/` (third-party text), so
every arm sees the same articles in the same batches.

Arms (same pool, same batches, the production `language_filter_batch`):
  claude_a     AGENT1B_LANG_MODE=claude        today's filter: a label for every article (the reference)
  claude_b     AGENT1B_LANG_MODE=claude        the same code again: Haiku's own run-to-run noise, the floor
  exceptions   AGENT1B_LANG_MODE=exceptions    the model lists only French / other articles

The owner's rule: against today's filter, (1) foreign-language articles let through must not rise and
(2) English or French articles wrongly dropped must not rise. With no hand labels yet, "today's filter"
(claude_a) is the reference, claude_b measures how often that reference disagrees with itself, and every
article the arms disagree on is written to the `_rows.json` file for the owner to settle by hand.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))
os.chdir(ROOT)
os.environ["LANGSMITH_TRACING"] = "false"

import agent1b_fetch_news as a1b  # noqa: E402
import config  # noqa: E402
from evals import cost, results  # noqa: E402
from evals.snapshot import PRIVATE_DIR  # noqa: E402
from evals.stats import rate_with_ci  # noqa: E402

POOL_CACHE = PRIVATE_DIR / "lang_filter_pool.json"

# Per-article budgets for the pre-run estimate (measured 2026-10-16: 62 calls, ~220k in / 24k out for 547).
_IN_PER_ARTICLE = 400
_OUT_PER_ARTICLE_FULL = 44
_OUT_PER_ARTICLE_EXC = 4


# ---------------------------------------------------------------------------
# Pure helpers (unit tested)
# ---------------------------------------------------------------------------

def labels_from_kept(batch: list, kept: list) -> dict:
    """{url: 'en'|'fr'|'dropped'} for one batch: a kept article keeps its label, anything else was dropped."""
    by_url = {a["url"]: a.get("language") for a in kept}
    return {a["url"]: by_url.get(a["url"], "dropped") for a in batch}


def compare(reference: dict, other: dict) -> dict:
    """Counts of how `other` differs from `reference` over the same urls.

    foreign_through: reference dropped it, other kept it  (rule 1: must not rise)
    wrongly_dropped: reference kept it, other dropped it  (rule 2: must not rise)
    relabelled:      both kept it, English vs French differs (changes which newsletter variant shows it)
    """
    c = {"foreign_through": [], "wrongly_dropped": [], "relabelled": [], "n": len(reference)}
    for url, ref in reference.items():
        got = other[url]
        if ref == got:
            continue
        if ref == "dropped":
            c["foreign_through"].append(url)
        elif got == "dropped":
            c["wrongly_dropped"].append(url)
        else:
            c["relabelled"].append(url)
    return c


def disagreements(arms: dict) -> list:
    """urls on which not all arms agree, with each arm's label."""
    names = list(arms)
    return [{"url": u, **{n: arms[n][u] for n in names}}
            for u in arms[names[0]] if len({arms[n][u] for n in names}) > 1]


def make_batches(articles: list) -> list:
    return [articles[i:i + a1b.LANG_BATCH_SIZE] for i in range(0, len(articles), a1b.LANG_BATCH_SIZE)]


def estimate(n_articles: int) -> float:
    inp = 3 * n_articles * _IN_PER_ARTICLE
    out = n_articles * (2 * _OUT_PER_ARTICLE_FULL + _OUT_PER_ARTICLE_EXC)
    return cost.estimate_cost(inp, out, config.SCORING_MODEL)


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------

class Meter:
    def __init__(self):
        self.input = self.output = self.calls = 0

    def record(self, response):
        u = getattr(response, "usage", None)
        self.calls += 1
        self.input += getattr(u, "input_tokens", 0) or 0
        self.output += getattr(u, "output_tokens", 0) or 0


class _Msgs:
    def __init__(self, inner, meter):
        self._inner, self._meter = inner, meter

    def create(self, **kw):
        r = self._inner.create(**kw)
        self._meter.record(r)
        return r


class Metered:
    def __init__(self, client, meter):
        self.messages = _Msgs(client.messages, meter)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def load_pool() -> list:
    if POOL_CACHE.exists():
        return json.loads(POOL_CACHE.read_text(encoding="utf-8"))
    articles = a1b.prefilter(a1b.fetch_hn_articles() + a1b.fetch_newsapi_articles())
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    POOL_CACHE.write_text(json.dumps(articles, ensure_ascii=False), encoding="utf-8")
    return articles


def run_arm(mode: str, batches: list, real_client) -> tuple[dict, Meter]:
    """Run one arm through the production batch function. Mode is read at call time, so set the env var."""
    os.environ["AGENT1B_LANG_MODE"] = mode
    meter = Meter()
    client = Metered(real_client, meter)
    labels: dict = {}
    # Sequential across arms (the env var is process-wide); parallel within an arm like production.
    with ThreadPoolExecutor(max_workers=a1b.MAX_CONCURRENT_CLAUDE_CALLS) as ex:
        futures = [ex.submit(a1b.language_filter_batch, b, i + 1, client) for i, b in enumerate(batches)]
        for batch, fut in zip(batches, futures):
            labels.update(labels_from_kept(batch, fut.result()))
    return labels, meter


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--approve", action="store_true", help="only after the owner OKs an estimate over 2 USD")
    args = ap.parse_args(argv)

    pool = load_pool()
    est = estimate(len(pool))
    print(f"{len(pool)} articles after prefilter ({len(make_batches(pool))} batches). Estimated cost ${est:.2f}")
    if args.dry_run:
        return 0
    cost.CostGuard(approved=args.approve).check(est)

    import anthropic
    key = os.environ.get("ANTHROPIC_1ST_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_1ST_API_KEY is not set")
    real = anthropic.Anthropic(api_key=key, timeout=60.0)
    batches = make_batches(pool)

    arms, meters = {}, {}
    for name, mode in (("claude_a", "claude"), ("claude_b", "claude"), ("exceptions", "exceptions")):
        arms[name], meters[name] = run_arm(mode, batches, real)
        m = meters[name]
        print(f"{name}: {m.calls} calls, {m.input} in / {m.output} out tokens", flush=True)

    n = len(pool)
    metrics = {}
    for name in ("claude_b", "exceptions"):
        c = compare(arms["claude_a"], arms[name])
        for key_, label in (("foreign_through", "foreign_through"), ("wrongly_dropped", "wrongly_dropped"),
                            ("relabelled", "relabelled")):
            metrics[f"{name}_vs_a_{label}"] = rate_with_ci(len(c[key_]), n)
    dropped_a = sum(1 for v in arms["claude_a"].values() if v == "dropped")
    metrics["claude_a_dropped"] = rate_with_ci(dropped_a, n)
    for name, m in meters.items():
        usd = cost.estimate_cost(m.input, m.output, config.SCORING_MODEL)
        metrics[f"cost_usd_{name}"] = {"value": round(usd, 4), "n": n, "ci_low": None, "ci_high": None}
    actual = sum(cost.estimate_cost(m.input, m.output, config.SCORING_MODEL) for m in meters.values())

    doc = results.build_results(
        f"lang_filter_eval_{date.today().isoformat()}", config.SCORING_MODEL, metrics, cost_usd=round(actual, 4),
        notes=("Fresh unfiltered fetch after prefilter, same batches per arm. Reference = today's filter (claude_a); "
               "claude_b is the same code again (noise floor). No hand labels yet: disagreements are in the rows file."))
    path = results.write_results(doc)
    rows = disagreements(arms)
    by_url = {a["url"]: a for a in pool}
    for r in rows:
        a = by_url[r["url"]]
        r.update(title=a.get("title"), domain=a1b.urlparse(a["url"]).hostname, description=(a.get("description") or "")[:200])
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    rows_path = PRIVATE_DIR / f"lang_filter_eval_{date.today().isoformat()}_rows.json"
    rows_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, m in metrics.items():
        print(f"  {name}: {m['value']}" + (f" [{m['ci_low']:.3f}, {m['ci_high']:.3f}]" if m.get("ci_low") is not None else ""))
    print(f"{len(rows)} articles with disagreement -> {rows_path.relative_to(ROOT)}; wrote {path.relative_to(ROOT)}; "
          f"actual eval cost ${actual:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
