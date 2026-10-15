# agents/agent2b_summarize_news.py

import anthropic
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompt_guard import GUARD_NEWS_SUMMARY
import stage_counter
import summary_sources
import tracing
from config import (
    DATA_DIR, SCORING_MODEL, NEWS_SUMMARY_MAX_TOKENS,
    GCP_PROJECT_ID, TOPIC_CONTENT_SUMMARIZED, FIRESTORE_COLLECTION, USE_FIRESTORE,
)
from article_fetch import fetch_article_text  # noqa: E402 — shared with agent1b
import news_shortlist  # noqa: E402 — AGENT2B_MODE switch; selection code is imported lazily

# --- Rate limiting ---
MAX_CONCURRENT_CLAUDE_CALLS = 3
MAX_FETCH_WORKERS = 20
_semaphore = threading.Semaphore(MAX_CONCURRENT_CLAUDE_CALLS)


def _is_twitter_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in ("x.com", "twitter.com", "www.x.com", "www.twitter.com")


# ---------------------------------------------------------------------------
# Claude call
# ---------------------------------------------------------------------------

def claude_call_with_retry(client: anthropic.Anthropic, max_retries: int = 4, **kwargs) -> object:
    """The concurrency slot is held for the call only, never while waiting to retry."""
    for attempt in range(max_retries):
        try:
            with _semaphore:
                return client.messages.create(**kwargs)
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            wait = 10 * (2 ** attempt)
            print(f"  [retry]    rate limited, waiting {wait}s...")
            time.sleep(wait)


def summarize_article(article: dict, text: str | None, prompt_template: str, fallback_template: str, quebec_style: str, client: anthropic.Anthropic) -> dict:
    title         = article.get("title", "")
    description   = article.get("description", "") or ""
    used_fallback = text is None
    style_instruction = f"\n{quebec_style}\n" if article.get("language") == "fr" else "\n"

    if used_fallback:
        if not description.strip():
            return {**article, "summary": None, "used_fallback": True, "summary_error": "no_content"}
        prompt = fallback_template.format(title=title, description=description, style_instruction=style_instruction)
    else:
        prompt = prompt_template.format(title=title, text=text, style_instruction=style_instruction)

    try:
        response = claude_call_with_retry(
            client,
            model=SCORING_MODEL,
            max_tokens=NEWS_SUMMARY_MAX_TOKENS,
            system=GUARD_NEWS_SUMMARY,
            messages=[{"role": "user", "content": prompt}]
        )
        if not response.content:
            raise RuntimeError("Empty Claude response content")
        summary = response.content[0].text.strip()
        if summary.upper() == "SKIP":
            return {**article, "summary": None, "used_fallback": used_fallback, "summary_error": "no_content"}
        # `_source` is popped by run() before anything is stored on the run doc.
        return {**article, "summary": summary, "used_fallback": used_fallback, "summary_error": None,
                "_source": description if used_fallback else text}
    except Exception as e:
        return {**article, "summary": None, "used_fallback": used_fallback, "summary_error": str(e)}


def process_article(args: tuple) -> dict:
    client, article, prompt_template, fallback_template, quebec_style = args
    url = article.get("url", "")

    if _is_twitter_url(url):
        return {**article, "summary": None, "used_fallback": False, "summary_error": "twitter_no_content"}

    text = fetch_article_text(url) if url else None
    return summarize_article(article, text, prompt_template, fallback_template, quebec_style, client)


def summarize_with_text(article: dict, text: str | None, prompt_template: str, fallback_template: str, quebec_style: str, client) -> dict:
    """process_article without the fetch: the text is already in hand (shortlist mode)."""
    if _is_twitter_url(article.get("url", "")):
        return {**article, "summary": None, "used_fallback": False, "summary_error": "twitter_no_content"}
    return summarize_article(article, text, prompt_template, fallback_template, quebec_style, client)


def run_shortlist(by_category: dict, all_articles: list, client, prompt_template: str, fallback_template: str,
                  quebec_style: str) -> tuple[dict, set, dict]:
    """AGENT2B_MODE=shortlist: fetch all texts, shortlist per category, summarize only the shortlist.

    Returns (results_by_index, kept_indices, summary). Indices are positions in `all_articles`.
    """
    def fetch(i: int):
        url = all_articles[i].get("url", "")
        return i, (None if (not url or _is_twitter_url(url)) else fetch_article_text(url))

    with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as executor:
        texts = dict(executor.map(fetch, range(len(all_articles))))
    print(f"  [shortlist] fetched {sum(1 for t in texts.values() if t)}/{len(texts)} article texts", flush=True)

    with open("prompts/article_selection_prompt.txt", "r", encoding="utf-8") as f:
        selection_prompt = f.read()
    kept, summary = news_shortlist.shortlist_all(by_category, texts, selection_prompt, client)
    print(f"  [shortlist] {len(kept)}/{len(all_articles)} articles shortlisted for summaries", flush=True)

    def summarize(i: int):
        return i, summarize_with_text(all_articles[i], texts[i], prompt_template, fallback_template, quebec_style, client)

    with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as executor:
        results_by_index = dict(executor.map(summarize, sorted(kept)))
    return results_by_index, kept, summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def increment_and_check(run_id: str) -> bool:
    """Count this stage once on the run doc. True if this call made both agent2a and agent2b done.

    Idempotent (see stage_counter.count_stage): a repeated or retried run never counts twice.
    """
    return stage_counter.count_stage(run_id, "agent2b_counted")


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


def assemble_results(by_category: dict, results_by_index: dict, only_indices: set | None = None) -> tuple[dict, list, list]:
    """Put each result back in its article's slot. Articles are matched by position, not URL,
    so repeated or empty URLs cannot overwrite each other. Returns (by_category, flat list, sources).

    `only_indices` (shortlist mode): articles outside it were never summarized on purpose and are
    left out of the output instead of being recorded as "not processed"."""
    summarized_by_category: dict[str, list] = {}
    all_summarized: list = []
    sources: list[dict] = []
    seen_urls: set = set()
    i = 0
    for category, articles in by_category.items():
        out = []
        for a in articles:
            result = results_by_index.get(i)
            i += 1
            if only_indices is not None and (i - 1) not in only_indices:
                continue
            if result is None:
                result = {**a, "summary": None, "used_fallback": False, "summary_error": "not processed"}
            else:
                result = dict(result)
                src = result.pop("_source", None)
                url = result.get("url", "")
                # The judge looks sources up by URL, so only the first article with a URL is stored.
                if src and url and url not in seen_urls:
                    seen_urls.add(url)
                    sources.append({"ident": url, "title": result.get("title", ""), "text": src,
                                    "used_fallback": result.get("used_fallback")})
            out.append(result)
            all_summarized.append(result)
        summarized_by_category[category] = out
    return summarized_by_category, all_summarized, sources


def run(run_id: str):
    """Main agent logic. Called by main.py (Cloud Run) or orchestrator.py."""
    start_time = datetime.now()

    try:
        with open("prompts/news_summary_prompt.txt", "r", encoding="utf-8") as f:
            prompt_template = f.read()

        with open("prompts/news_summary_fallback_prompt.txt", "r", encoding="utf-8") as f:
            fallback_template = f.read()

        with open("prompts/quebec_french_style.txt", "r", encoding="utf-8") as f:
            quebec_style = f.read()

        if USE_FIRESTORE:
            from google.cloud import firestore as _fs
            doc_snap = _fs.Client(project=GCP_PROJECT_ID).collection(FIRESTORE_COLLECTION).document(run_id).get()
            doc      = doc_snap.to_dict()
            if not doc:
                raise RuntimeError(f"[agent2b] Firestore document not found for run_id={run_id}")
            filtered = doc.get("news_filtered")
            if filtered is None:
                raise RuntimeError(f"[agent2b] 'news_filtered' missing from Firestore document run_id={run_id}")
            print(f"[agent2b]  Loaded news_filtered from Firestore")
        else:
            in_path = os.path.join(DATA_DIR, "news_filtered.json")
            with open(in_path, "r", encoding="utf-8") as f:
                filtered = json.load(f)

        by_category: dict = filtered.get("by_category", {})
        all_articles: list = [a for arts in by_category.values() for a in arts]

        print(f"Summarizing {len(all_articles)} articles across {len(by_category)} categories...\n")

        client = tracing.make_client("agent2b", run_id)
        tasks  = [(client, article, prompt_template, fallback_template, quebec_style) for article in all_articles]

        results_by_index: dict[int, dict] = {}
        only_indices = None
        shortlist_summary = None
        done = 0

        if news_shortlist.agent2b_mode() == news_shortlist.MODE_SHORTLIST:
            print("[agent2b]  AGENT2B_MODE=shortlist: summarizing only the shortlisted articles", flush=True)
            results_by_index, only_indices, shortlist_summary = run_shortlist(
                by_category, all_articles, client, prompt_template, fallback_template, quebec_style)
        else:
            with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as executor:
                future_to_index = {executor.submit(process_article, t): i for i, t in enumerate(tasks)}
                for future in as_completed(future_to_index):
                    results_by_index[future_to_index[future]] = future.result()
                    done += 1
                    if done % 50 == 0 or done == len(tasks):
                        failed_so_far   = sum(1 for r in results_by_index.values() if not r.get("summary"))
                        fallback_so_far = sum(1 for r in results_by_index.values() if r.get("used_fallback"))
                        print(f"  [{done}/{len(tasks)}] failed={failed_so_far} fallback={fallback_so_far}")

        summarized_by_category, all_summarized, sources = assemble_results(by_category, results_by_index, only_indices)

        total_summarized = sum(1 for a in all_summarized if a.get("summary"))
        total_fallback   = sum(1 for a in all_summarized if a.get("used_fallback"))
        total_failed     = sum(1 for a in all_summarized if not a.get("summary"))

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n--- Done in {elapsed:.1f}s ---")
        print(f"Summarized:      {total_summarized}/{len(all_articles)}")
        print(f"Used fallback:   {total_fallback}")
        print(f"Failed entirely: {total_failed}")

        if USE_FIRESTORE:
            from google.cloud import firestore as _fs
            run_update = {"news_summaries": summarized_by_category}
            if shortlist_summary is not None:
                run_update["agent2b_shortlist_summary"] = shortlist_summary
            _fs.Client(project=GCP_PROJECT_ID).collection(FIRESTORE_COLLECTION).document(run_id).update(run_update)
            print(f"[agent2b]  Saved news_summaries to Firestore (run_id={run_id})")
            n_src = summary_sources.save_sources(_fs.Client(project=GCP_PROJECT_ID), run_id, "news", sources)
            print(f"[agent2b]  Saved {n_src} summary sources for the judge")
        else:
            os.makedirs(DATA_DIR, exist_ok=True)
            out_path = os.path.join(DATA_DIR, "news_summaries.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({
                    "run_at":              start_time.isoformat(),
                    "elapsed_seconds":     elapsed,
                    "total_articles":      len(all_articles),
                    "total_summarized":    total_summarized,
                    "total_used_fallback": total_fallback,
                    "total_failed":        total_failed,
                    "by_category":         summarized_by_category,
                    "articles":            all_summarized,
                    **({"shortlist": shortlist_summary} if shortlist_summary is not None else {}),
                }, f, indent=2, ensure_ascii=False)
            print(f"Saved to {out_path}")

        if USE_FIRESTORE:
            should_trigger = increment_and_check(run_id)
            if should_trigger:
                from google.cloud import pubsub_v1
                publisher  = pubsub_v1.PublisherClient()
                topic_path = publisher.topic_path(GCP_PROJECT_ID, TOPIC_CONTENT_SUMMARIZED)
                data       = json.dumps({"run_id": run_id}).encode("utf-8")
                publisher.publish(topic_path, data).result(timeout=30)
                stage_counter.mark_published(run_id)
                print(f"[agent2b]  Both agent2s done — published to {TOPIC_CONTENT_SUMMARIZED} (run_id={run_id})")
            else:
                print(f"[agent2b]  Waiting for agent2a to finish before triggering agent3")
    except Exception as e:
        _record_failure(run_id, "agent2b", e)
        raise
    finally:
        tracing.flush()


if __name__ == "__main__":
    run(run_id="local-debug")