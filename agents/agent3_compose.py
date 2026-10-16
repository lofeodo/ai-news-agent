# agents/agent3_compose.py

import anthropic
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompt_guard import GUARD_XML_TAGS, neutralize_tags
import tracing
import topics
from agent3_dedup_graph import dedup_section
import newsletter_def
import render
from render.blocks import render_article_card, render_paper_card  # noqa: F401  (re-exported)
from render.palette import AMBER as _AMBER, darken as _darken  # noqa: F401  (re-exported)
from render.text import esc as _esc, safe_url as _safe_url, strip_markdown_headers as _strip_markdown_headers  # noqa: F401
from config import DATA_DIR, SCORING_MODEL, GCP_PROJECT_ID, USE_FIRESTORE, FIRESTORE_COLLECTION, MAX_SCORE, newsletter_send_date, section_cap, RUNNERS_UP_MAX

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEWSLETTER_NAME              = "Latent SpaceMail"

NEWS_CATEGORIES = [
    "Model & Product Releases",
    "Policy, Law & Regulation",
    "Open Source & Tools",
    "Safety & Alignment",
    "Industry & Business",
    "Society & Culture",
    "Canada & Montreal",
]

NEWSLETTER_VARIANTS = {
    "0_0": {"include_french": False, "include_canada": False},
    "1_0": {"include_french": True,  "include_canada": False},
    "0_1": {"include_french": False, "include_canada": True},
    "1_1": {"include_french": True,  "include_canada": True},
}

SELECTION_MAX_TOKENS = 200
INTRO_MAX_TOKENS     = 300

_PROMPT_INJECTION_GUARD = GUARD_XML_TAGS


# ---------------------------------------------------------------------------
# Claude helpers
# ---------------------------------------------------------------------------

def claude_call_with_retry(client: anthropic.Anthropic, max_retries: int = 4, **kwargs) -> object:
    for attempt in range(max_retries):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            wait = 10 * (2 ** attempt)
            print(f"  [retry]  rate limited, waiting {wait}s...")
            time.sleep(wait)


# ---------------------------------------------------------------------------
# Article selection — pre-tagging helpers
# ---------------------------------------------------------------------------

# Keywords that identify named model releases from major AI labs.
# Used to tag articles [NAMED RELEASE] so Claude doesn't have to scan blindly.
_MODEL_RELEASE_KEYWORDS: dict[str, list[str]] = {
    "Anthropic":       ["fable 5", "fable5", "claude fable", "mythos 5", "mythos5"],
    "OpenAI":          ["gpt-5", "gpt5", "gpt-4o", "gpt-4.5", "o3 mini", "o4-mini", "o4 mini", "codex"],
    "Google DeepMind": ["gemini 3", "gemini3", "gemma 3", "gemma3", "diffusiongemma", "gemini 2.5"],
    "Meta AI":         ["llama 4", "llama4", "llama-4"],
    "Mistral":         ["mistral large", "mistral small", "codestral", "pixtral", "mistral nemo"],
    "xAI":             ["grok 3", "grok3", "grok-3"],
    "DeepSeek":        ["deepseek v4", "deepseek-v4", "deepseek r2", "deepseek-r2"],
    "GLM":             ["glm 5", "glm5", "glm-5"],
    "Kimi":            ["kimi k2", "kimik2"],
}


def _article_tag(article: dict, category: str) -> str:
    """Return "[NAMED RELEASE] " if the article mentions a specific model release from
    a major AI lab (Model & Product Releases category only), else "".
    """
    if category == "Model & Product Releases":
        text = (
            (article.get("title") or "") + " " +
            (article.get("summary") or "") +
            " " + (article.get("description") or "")
        ).lower()
        for _lab, kws in _MODEL_RELEASE_KEYWORDS.items():
            if any(kw in text for kw in kws):
                return "[NAMED RELEASE] "

    return ""


def _hn_label(article: dict, scores: list[int]) -> str:
    """HN points plus rank among the section's HN-scored articles (ties share a rank).

    Rank is shown because vote counts vary a lot by topic — a section's top story
    may have far fewer points than another section's.
    """
    hn = article.get("hn_score")
    if hn is None:
        return "hn: unknown (not posted to HN)"
    if article.get("hn_rank") and article.get("hn_of"):
        # Shortlist mode: the rank was computed over the whole section before the pool was cut down.
        return f"hn: {hn} points (#{article['hn_rank']} of {article['hn_of']} HN stories in this section)"
    rank = 1 + sum(1 for s in scores if s > hn)
    return f"hn: {hn} points (#{rank} of {len(scores)} HN stories in this section)"


def format_articles_for_selection(articles: list, category: str = "") -> str:
    hn_scores = [a["hn_score"] for a in articles if a.get("hn_score") is not None]
    lines = []
    for i, a in enumerate(articles):
        hn = _hn_label(a, hn_scores)
        tag = _article_tag(a, category)
        fallback_note = " [summary from description only]" if a.get("used_fallback") else ""
        lines.append(
            f"<article_{i}>\n"
            f"[{i}] {tag}{neutralize_tags(a.get('title', 'No title'))}\n"
            f"    {hn}{fallback_note}\n"
            f"    Summary: {neutralize_tags((a.get('summary') or a.get('description') or '')[:300])}\n"
            f"</article_{i}>"
        )
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Article selection
# ---------------------------------------------------------------------------

def parse_indices(response_text: str, max_index: int) -> list[int]:
    try:
        clean   = re.sub(r"```[a-z]*", "", response_text).strip()
        indices = json.loads(clean)
        if not isinstance(indices, list):
            return []
        return [i for i in indices if isinstance(i, int) and 0 <= i < max_index]
    except Exception as e:
        print(f"  [warn]   failed to parse indices: {e} — raw: {response_text[:100]}")
        return []


def parse_selection(response_text: str, max_index: int) -> tuple[list[int], list[int]]:
    """Parse the selection reply into (selected, runners_up) index lists.

    Accepts {"selected": [...], "runners_up": [...]} or a bare array (no runners-up).
    Repeats, out-of-range indices and runners-up that are also picks are dropped.
    """
    try:
        clean = re.sub(r"```[a-z]*", "", response_text).strip()
        data = json.loads(clean)
    except Exception as e:
        print(f"  [warn]   failed to parse indices: {e} — raw: {response_text[:100]}")
        return [], []
    if isinstance(data, dict):
        raw_sel, raw_run = data.get("selected"), data.get("runners_up")
    else:
        raw_sel, raw_run = data, None

    def valid(raw):
        if not isinstance(raw, list):
            return []
        return list(dict.fromkeys(i for i in raw if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < max_index))

    selected = valid(raw_sel)
    runners_up = [i for i in valid(raw_run) if i not in selected]
    return selected, runners_up


@dataclass
class SelectionResult:
    picks: list = field(default_factory=list)
    runners_up: list = field(default_factory=list)   # ranked fallbacks, best first


def _top_up_order(ordered: list, exclude: set) -> list[int]:
    """Indices not in `exclude`, by HN score descending (none last), ties in shuffled order."""
    rest = [i for i in range(len(ordered)) if i not in exclude]
    return sorted(rest, key=lambda i: (ordered[i].get("hn_score") is None, -(ordered[i].get("hn_score") or 0), i))


def _cap(category: str, topic=None) -> int:
    return topic.section_cap(category) if topic else section_cap(category)


def select_with_runners_up(
    category: str,
    articles: list,
    prompt_template: str,
    client: anthropic.Anthropic,
    topic=None,
) -> SelectionResult:
    if not articles:
        return SelectionResult()

    # Drop articles with no summary and no description — they would render as blank cards
    before = len(articles)
    articles = [a for a in articles if (a.get("summary") or "").strip() or (a.get("description") or "").strip()]
    dropped = before - len(articles)
    if dropped:
        print(f"  [select] '{category}': dropped {dropped} article(s) with no content")

    if not articles:
        return SelectionResult()

    # Neutral order: list position must not carry a quality signal (sorting by HN put
    # every HN article above every NewsAPI one). Sorting by URL first removes the
    # thread-timing order inherited from agent1b, so the shuffle is reproducible.
    ordered = sorted(articles, key=lambda a: a.get("url") or "")
    random.Random(category).shuffle(ordered)

    formatted = format_articles_for_selection(ordered, category)
    cap       = _cap(category, topic)
    prompt    = prompt_template.format(category=category, articles=formatted, cap=cap, runners_up=RUNNERS_UP_MAX)

    response = claude_call_with_retry(
        client,
        model=SCORING_MODEL,
        max_tokens=SELECTION_MAX_TOKENS,
        system=_PROMPT_INJECTION_GUARD,
        messages=[{"role": "user", "content": prompt}],
    )

    if not response.content:
        raise RuntimeError(f"Empty Claude response for article selection in '{category}'")
    indices, runners = parse_selection(response.content[0].text, len(ordered))

    if not indices:
        print(f"  [warn]   no valid indices for '{category}' — falling back to first {cap}")
        indices = list(range(min(cap, len(ordered))))
        runners = [i for i in runners if i not in indices]

    trimmed = []
    if len(indices) > cap:
        print(f"  [select] '{category}': model picked {len(indices)}, capped at {cap}")
        trimmed = indices[cap:]
        indices = indices[:cap]  # the model lists most valuable first

    # Pool order: picks the cap trimmed (the model liked them), the model's runners-up,
    # then a deterministic top-up so the pool is as deep as the section allows.
    pool = list(dict.fromkeys(trimmed + runners))[:RUNNERS_UP_MAX]
    extra = _top_up_order(ordered, set(indices) | set(pool))[:RUNNERS_UP_MAX - len(pool)]
    if extra:
        print(f"  [select] '{category}': topped up runners-up with {len(extra)} by HN score")
    pool += extra

    return SelectionResult([ordered[i] for i in indices], [ordered[i] for i in pool])


def select_articles_for_category(
    category: str,
    articles: list,
    prompt_template: str,
    client: anthropic.Anthropic,
) -> list[dict]:
    return select_with_runners_up(category, articles, prompt_template, client).picks


def take_fallback(category: str, picks: list, runners_up: list, used=(), topic=None):
    """Next runner-up to add to a section, or None.

    Only fills a gap: None when `picks` (the articles currently kept) already
    reach the section's cap. Skips runners-up already kept or in `used`
    (everything the dedup loop has already tried, removed ones included).
    """
    if len(picks) >= _cap(category, topic):
        return None
    taken = {id(a) for a in picks} | {id(a) for a in used}
    return next((a for a in runners_up if id(a) not in taken), None)


# ---------------------------------------------------------------------------
# Intro paragraph
# ---------------------------------------------------------------------------

def write_intro(
    papers: list,
    selected_by_category: dict[str, list],
    prompt_template: str,
    client: anthropic.Anthropic,
) -> str:
    paper_lines = "\n".join(
        f"<paper>- {neutralize_tags(p['title'])} (score: {(p.get('scores') or {}).get('total', 0)}/{MAX_SCORE})</paper>"
        for p in papers
    )

    # Flatten all selected articles, annotate with category + HN score, sort by
    # HN score descending so the prompt sees high-signal items first.
    all_headlines = []
    for cat, arts in selected_by_category.items():
        for a in arts[:2]:
            hn = a.get("hn_score")
            hn_str = f" [HN:{hn}]" if hn is not None else ""
            all_headlines.append((hn or -1, f"<headline>- [{cat}]{hn_str} {neutralize_tags(a.get('title', ''))}</headline>"))
    all_headlines.sort(key=lambda x: x[0], reverse=True)
    headline_lines = [line for _, line in all_headlines]

    prompt = prompt_template.format(
        date=newsletter_send_date().strftime("%B %d, %Y"),
        papers=paper_lines,
        headlines="\n".join(headline_lines[:15]),
    )

    response = claude_call_with_retry(
        client,
        model=SCORING_MODEL,
        max_tokens=INTRO_MAX_TOKENS,
        system=_PROMPT_INJECTION_GUARD,
        messages=[{"role": "user", "content": prompt}],
    )

    if not response.content:
        raise RuntimeError("Empty Claude response from intro writer")
    return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# HTML composition — the building blocks live in agents/render/
# ---------------------------------------------------------------------------

def require_summarized_papers(papers: list) -> list:
    """Keep only papers that have a summary; an issue with no summarized spotlight is not composed."""
    kept = [p for p in papers if (p.get("summary") or "").strip()]
    if not kept:
        raise RuntimeError("[agent3] no spotlight paper has a summary")
    return kept


def compose_html(
    intro: str,
    papers: list,
    selected_by_category: dict[str, list],
    week_of: str,
    include_canada: bool = True,
    topic=None,
) -> str:
    """The newsletter email. `topic` (default: the AI newsletter) supplies the name, tagline, accent colour,
    sections and icons; everything else is the shared layout. Drawn by the block renderer from the topic's
    ready-made definition (newsletter_def.from_topic)."""
    topic = topic or topics.get_topic()
    content = render.Content(
        intro=intro,
        sections={(topic.id, cat): arts for cat, arts in selected_by_category.items()},
        widgets={"paper_spotlight": {"papers": papers}},
    )
    return render.render(newsletter_def.from_topic(topic), content, render.Look.from_topic(topic, week_of),
                         {"include_canada": include_canada}).html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _dedup(category: str, sel: "SelectionResult", client, run_id: str, pass_name: str, audits: list, topic=None) -> list:
    """Run the dedup graph on one selection; records its audit. Never raises (dedup_section degrades)."""
    result = dedup_section(category, sel.picks, sel.runners_up, client,
                           create=lambda **kw: claude_call_with_retry(client, **kw),
                           run_id=run_id, pass_name=pass_name, topic=topic)
    audits.append({**result.audit, "usage": result.usage})
    return result.picks


def summarize_dedup(audits: list) -> dict:
    """Counts only, small enough for the run doc."""
    return {
        "sections_checked":  sum(1 for a in audits if a["status"] in ("ok", "degraded")),
        "degraded":          sum(1 for a in audits if a["status"] == "degraded"),
        "duplicates_removed": sum(len(g["removed"]) for a in audits for g in a["groups"]),
        "fallbacks_added":   sum(1 for a in audits for f in a["fallbacks"] if f["accepted"]),
        "input_tokens":      sum(a.get("usage", {}).get("input_tokens", 0) for a in audits),
        "output_tokens":     sum(a.get("usage", {}).get("output_tokens", 0) for a in audits),
    }


def write_dedup_audit(run_id: str, audits: list) -> None:
    """Detailed audit to data/ and, in cloud mode, Firestore agent3_audits/{run_id}. Best effort."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "agent3_dedup_log.json"), "w", encoding="utf-8") as f:
            json.dump({"run_id": run_id, "sections": audits}, f, indent=2, ensure_ascii=False)
        if USE_FIRESTORE:
            from google.cloud import firestore as _fs
            _fs.Client(project=GCP_PROJECT_ID).collection("agent3_audits").document(run_id).set(
                {"run_id": run_id, "sections": audits})
    except Exception as e:
        print(f"  [dedup] could not write audit log: {e}")


def build_run_doc_update(newsletter_variants: dict, subject: str, selected_all: dict,
                         selected_en: dict, by_category: dict, dedup_summary: dict | None = None, topic=None) -> dict:
    """Fields agent3 writes to the run doc when it finishes.

    The doc must stay under Firestore's 1 MiB cap, and the full article pools
    (~850 KB at ~500 articles) plus the variants (~420 KB) don't fit (2026-09-28).
    Nothing reads the pools after this point, so shrink them in the same write:
    news_summaries keeps only what shipped, news_filtered becomes counts.
    Both must stay truthy: agent_healthcheck checks them for presence.
    """
    shipped_by_category = {}
    for category in (topic.category_names if topic else NEWS_CATEGORIES):
        seen, shipped = set(), []
        for a in selected_all.get(category, []) + selected_en.get(category, []):
            if a.get("url") not in seen:
                seen.add(a.get("url"))
                shipped.append(a)
        if shipped:
            shipped_by_category[category] = shipped
    update = {
        "newsletter_variants":  newsletter_variants,
        "newsletter_html":      newsletter_variants["0_0"],
        "newsletter_subject":   subject,
        "newsletter_composed":  True,
        "news_summaries":       shipped_by_category,
        "news_filtered":        {
            "pruned_by_agent3": True,
            "article_counts":   {cat: len(arts) for cat, arts in by_category.items()},
        },
    }
    if dedup_summary is not None:
        update["agent3_dedup_summary"] = dedup_summary
    return update


def _record_failure(run_id: str, agent_name: str, error: Exception) -> None:
    """Best-effort write of a top-level run failure to Firestore, for health checks."""
    if not USE_FIRESTORE:
        return
    try:
        from google.cloud import firestore
        firestore.Client(project=GCP_PROJECT_ID).collection(FIRESTORE_COLLECTION).document(run_id).set(
            {
                f"{agent_name}_error": str(error),
                f"{agent_name}_failed_at": datetime.now(timezone.utc).isoformat(),
            },
            merge=True
        )
        print(f"[{agent_name}]  Recorded failure to Firestore (run_id={run_id})", flush=True)
    except Exception as record_error:
        print(f"[{agent_name}]  Failed to record failure to Firestore: {record_error}", flush=True)


def run(run_id: str, force: bool = False):
    """Main agent logic. Called by main.py (Cloud Run) or orchestrator.py.

    In cloud mode a run that is already composed is left alone unless `force` is set: the first compose
    shrinks the article pools on the run doc, so a second one would build from what is left.
    """
    start_time = datetime.now()

    try:
        if USE_FIRESTORE:
            from google.cloud import firestore as _fs
            doc_snap    = _fs.Client(project=GCP_PROJECT_ID).collection(FIRESTORE_COLLECTION).document(run_id).get()
            doc         = doc_snap.to_dict()
            if not doc:
                raise RuntimeError(f"[agent3] Firestore document not found for run_id={run_id}")
            papers      = doc.get("paper_summaries")
            by_category = doc.get("news_summaries")
            if papers is None or by_category is None:
                raise RuntimeError(f"[agent3] 'paper_summaries' or 'news_summaries' missing from Firestore document run_id={run_id}")
            if doc.get("newsletter_composed"):
                if not force:
                    print(f"[agent3]  run {run_id} is already composed, skipping (pass force to recompose)", flush=True)
                    return
                if (doc.get("news_filtered") or {}).get("pruned_by_agent3"):
                    print("[agent3]  WARNING: force on a composed run, whose article pools were already "
                          "shrunk. Re-run agent2a and agent2b first or the issue is built from what is left.", flush=True)
            print(f"[agent3]  Loaded paper_summaries and news_summaries from Firestore")
        else:
            with open(os.path.join(DATA_DIR, "paper_summaries.json"), "r", encoding="utf-8") as f:
                paper_data = json.load(f)
            with open(os.path.join(DATA_DIR, "news_summaries.json"), "r", encoding="utf-8") as f:
                news_data = json.load(f)
            papers      = paper_data["papers"]
            by_category = news_data["by_category"]

        topic = topics.get_topic(topics.topic_id_of(doc) if USE_FIRESTORE else os.environ.get("TOPIC"))
        print(f"[agent3]  topic: {topic.id}", flush=True)
        selection_prompt = topic.read_filled_prompt("selection")
        intro_prompt     = topic.read_filled_prompt("intro")
        if topic.spotlight == "papers":
            papers = require_summarized_papers(papers)
        client = tracing.make_client("agent3", run_id)

        print("\n=== Selecting articles per category ===")
        # Two selection passes per category:
        #   selected_all — full pool (French + English), used when include_french=True
        #   selected_en  — English-only pool, used when include_french=False
        # The second pass is skipped when the category has no French articles.
        selected_all: dict[str, list] = {}
        selected_en:  dict[str, list] = {}
        dedup_audits: list[dict] = []

        for category in topic.category_names:
            articles = by_category.get(category, [])
            print(f"  [{category}] {len(articles)} articles available", end="")

            if not articles:
                print(" → no articles, using fallback")
                selected_all[category] = []
                selected_en[category]  = []
                continue

            sel = select_with_runners_up(category, articles, selection_prompt, client, topic)
            full_selected = _dedup(category, sel, client, run_id, "all", dedup_audits, topic)
            selected_all[category] = full_selected

            en_articles = [a for a in articles if a.get("language") != "fr"]
            if len(en_articles) < len(articles):
                print(f" → selected {len(full_selected)} (re-running English-only)", end="")
                sel_en = select_with_runners_up(category, en_articles, selection_prompt, client, topic)
                selected_en[category] = _dedup(category, sel_en, client, run_id, "en", dedup_audits, topic)
            else:
                selected_en[category] = full_selected

            print(f" → {len(full_selected)} (all) / {len(selected_en[category])} (en)")

        write_dedup_audit(run_id, dedup_audits)

        print("\n=== Writing intro paragraph ===")
        intro = write_intro(papers, selected_all, intro_prompt, client)
        print(f"  Intro: {intro[:100]}...")

        print("\n=== Composing HTML variants ===")
        week_of = newsletter_send_date().strftime("%B %d, %Y")
        newsletter_variants: dict[str, str] = {}
        for key, prefs in topic.variant_prefs().items():
            selection = selected_all if prefs["include_french"] else selected_en
            html = compose_html(intro, papers, selection, week_of, include_canada=prefs["include_canada"], topic=topic)
            newsletter_variants[key] = html
            print(f"  Variant {key}: {len(html):,} chars")

        print("\n=== Saving newsletter HTML ===")
        os.makedirs(DATA_DIR, exist_ok=True)
        for key, html in newsletter_variants.items():
            path = os.path.join(DATA_DIR, f"newsletter_{key}.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"  Saved {path}")
        legacy_path = os.path.join(DATA_DIR, "newsletter.html")
        with open(legacy_path, "w", encoding="utf-8") as f:
            f.write(newsletter_variants["0_0"])
        print(f"  Saved {legacy_path} (legacy alias for 0_0)")

        if USE_FIRESTORE:
            from google.cloud import firestore as _fs
            # The run doc must stay under Firestore's 1 MiB cap; see build_run_doc_update().
            _fs.Client(project=GCP_PROJECT_ID).collection(FIRESTORE_COLLECTION).document(run_id).update(
                build_run_doc_update(newsletter_variants, f"{topic.name} — {week_of}",
                                     selected_all, selected_en, by_category, summarize_dedup(dedup_audits), topic)
            )
            print(f"  Written newsletter_variants + newsletter_html (0_0) to Firestore; pruned article pools")

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n--- Done in {elapsed:.1f}s ---")

        total_selected = sum(len(v) for v in selected_all.values())
        print(f"Papers:   {len(papers)}")
        print(f"Articles: {total_selected} selected across {len(topic.categories)} categories")
        print(f"Variants: {list(newsletter_variants.keys())}")
    except Exception as e:
        _record_failure(run_id, "agent3", e)
        raise
    finally:
        tracing.flush()


if __name__ == "__main__":
    run(run_id="local-debug")