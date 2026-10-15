# agents/news_shortlist.py
#
# Optional agent2b mode (AGENT2B_MODE=shortlist): decide which articles are worth a summary *before*
# paying for one. Cost roadmap Step 3, option A. The default mode ("all") is the original behavior and
# never imports this module's selection path.
#
#   all        summarize every article (~490 Claude calls), agent3 then picks from the summaries.
#   shortlist  fetch every article's text (as before, no Claude), let agent3's own selector pick the
#              best articles per category from title + HN points + the first ~300 characters of the
#              page, summarize only those picks and their runners-up, hand agent3 that smaller pool.
#
# agent3 still makes the final choice from real summaries, so this is a coarse first cut, not a
# replacement. Anything that goes wrong for a category keeps the whole category (today's behavior),
# so a failure here can cost money but never drops news.

import os
from concurrent.futures import ThreadPoolExecutor

MODE_ALL = "all"
MODE_SHORTLIST = "shortlist"

SNIPPET_CHARS = 300  # agent3's selection prompt shows at most this much of the summary/description


def agent2b_mode() -> str:
    """AGENT2B_MODE, read on every call (no redeploy needed to switch). Anything unknown means 'all'."""
    mode = (os.environ.get("AGENT2B_MODE") or MODE_ALL).strip().lower()
    return MODE_SHORTLIST if mode == MODE_SHORTLIST else MODE_ALL


def lead_text(article: dict, text: str | None, limit: int = SNIPPET_CHARS) -> str:
    """What the selector sees in place of a summary: the description when there is one (NewsAPI),
    otherwise the start of the fetched page (Hacker News stories have no description)."""
    desc = " ".join((article.get("description") or "").split())
    if desc:
        return desc[:limit]
    return " ".join((text or "").split())[:limit]


def make_candidates(articles: list, texts: list) -> list:
    """Copies of `articles` shaped for the selector: no summary, a lead-text description, and `_idx`
    (position in `articles`) so the picks can be mapped back."""
    out = []
    for i, (a, t) in enumerate(zip(articles, texts)):
        out.append({**a, "summary": None, "description": lead_text(a, t), "used_fallback": False, "_idx": i})
    return out


def _default_select():
    from agent3_compose import select_with_runners_up  # lazy: only shortlist mode needs agent3's code
    return select_with_runners_up


def shortlist_category(category, articles, texts, prompt_template, client, select_fn=None):
    """Indices (into `articles`) worth summarizing for one category.

    Two passes like agent3: the full pool, and the English-only pool when French articles exist, so
    both newsletter variants have candidates. Picks plus runners-up of each pass are kept.
    """
    select = select_fn or _default_select()
    candidates = make_candidates(articles, texts)
    kept: set[int] = set()

    def run_pass(pool):
        res = select(category, pool, prompt_template, client)
        kept.update(a["_idx"] for a in res.picks)
        kept.update(a["_idx"] for a in res.runners_up)

    run_pass(candidates)
    english = [c for c in candidates if c.get("language") != "fr"]
    if 0 < len(english) < len(candidates):
        run_pass(english)
    return kept


def shortlist_all(by_category: dict, texts: dict, prompt_template: str, client,
                  select_fn=None, max_workers: int = 7):
    """(kept, summary). `kept` holds global article indices in agent2b's flattened order;
    `texts` maps that same global index to the fetched text (or None).

    A category whose selection raises is kept whole and flagged `failed` in the summary.
    """
    layout = []   # (category, articles, first global index)
    start = 0
    for category, articles in by_category.items():
        layout.append((category, articles, start))
        start += len(articles)

    def one(item):
        category, articles, offset = item
        if not articles:
            return category, set(), False
        local_texts = [texts.get(offset + i) for i in range(len(articles))]
        try:
            local = shortlist_category(category, articles, local_texts, prompt_template, client, select_fn)
            return category, {offset + i for i in local}, False
        except Exception as e:
            print(f"  [shortlist] '{category}': selection failed ({e}); keeping the whole category", flush=True)
            return category, {offset + i for i in range(len(articles))}, True

    kept: set[int] = set()
    by_cat = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for (category, articles, _), (_, idxs, failed) in zip(layout, ex.map(one, layout)):
            kept |= idxs
            by_cat[category] = {"in": len(articles), "kept": len(idxs), "failed": failed}
    summary = {"mode": MODE_SHORTLIST, "articles_in": start, "shortlisted": len(kept), "by_category": by_cat}
    return kept, summary
