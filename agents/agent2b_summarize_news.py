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
from config import (
    DATA_DIR, SCORING_MODEL, NEWS_SUMMARY_MAX_TOKENS,
    GCP_PROJECT_ID, TOPIC_CONTENT_SUMMARIZED, FIRESTORE_COLLECTION, USE_FIRESTORE,
)
from article_fetch import fetch_article_text  # noqa: E402 — shared with agent1b

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
    for attempt in range(max_retries):
        try:
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
        with _semaphore:
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
        return {**article, "summary": summary, "used_fallback": used_fallback, "summary_error": None}
    except Exception as e:
        return {**article, "summary": None, "used_fallback": used_fallback, "summary_error": str(e)}


def process_article(args: tuple) -> dict:
    client, article, prompt_template, fallback_template, quebec_style = args
    url = article.get("url", "")

    if _is_twitter_url(url):
        return {**article, "summary": None, "used_fallback": False, "summary_error": "twitter_no_content"}

    text = fetch_article_text(url) if url else None
    return summarize_article(article, text, prompt_template, fallback_template, quebec_style, client)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def increment_and_check(run_id: str) -> bool:
    """
    Atomically increment agent2_completions in Firestore using a transaction.
    Returns True if this agent pushed the count to 2 (both agent2a and agent2b done).

    Idempotent: main.py may re-run agent2b after a crash, and an attempt that
    dies after incrementing must not count twice — that would reach 2 before
    agent2a finished and trigger agent3 without paper_summaries.
    """
    from google.cloud import firestore

    db  = firestore.Client(project=GCP_PROJECT_ID)
    ref = db.collection(FIRESTORE_COLLECTION).document(run_id)

    @firestore.transactional
    def _increment(transaction, ref):
        snapshot = ref.get(transaction=transaction)
        data     = snapshot.to_dict() or {}
        if data.get("agent2b_counted"):
            return data.get("agent2_completions", 0)
        new_val  = data.get("agent2_completions", 0) + 1
        transaction.update(ref, {"agent2_completions": new_val, "agent2b_counted": True})
        return new_val

    count = _increment(db.transaction(), ref)
    print(f"[agent2b]  agent2_completions = {count}")
    return count >= 2


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

        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_1ST_API_KEY"))
        tasks  = [(client, article, prompt_template, fallback_template, quebec_style) for article in all_articles]

        results_by_url: dict[str, dict] = {}
        done = 0

        with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as executor:
            future_to_article = {executor.submit(process_article, t): t[1] for t in tasks}
            for future in as_completed(future_to_article):
                result = future.result()
                url    = result.get("url", "")
                results_by_url[url] = result
                done += 1
                if done % 50 == 0 or done == len(tasks):
                    failed_so_far   = sum(1 for r in results_by_url.values() if not r.get("summary"))
                    fallback_so_far = sum(1 for r in results_by_url.values() if r.get("used_fallback"))
                    print(f"  [{done}/{len(tasks)}] failed={failed_so_far} fallback={fallback_so_far}")

        summarized_by_category: dict[str, list] = {}
        for category, articles in by_category.items():
            summarized_by_category[category] = [
                results_by_url.get(a.get("url", ""), {
                    **a,
                    "summary":       None,
                    "used_fallback": False,
                    "summary_error": "not processed",
                })
                for a in articles
            ]

        all_summarized   = list(results_by_url.values())
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
            _fs.Client(project=GCP_PROJECT_ID).collection(FIRESTORE_COLLECTION).document(run_id).update({
                "news_summaries": summarized_by_category
            })
            print(f"[agent2b]  Saved news_summaries to Firestore (run_id={run_id})")
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
                print(f"[agent2b]  Both agent2s done — published to {TOPIC_CONTENT_SUMMARIZED} (run_id={run_id})")
            else:
                print(f"[agent2b]  Waiting for agent2a to finish before triggering agent3")
    except Exception as e:
        _record_failure(run_id, "agent2b", e)
        raise


if __name__ == "__main__":
    run(run_id="local-debug")