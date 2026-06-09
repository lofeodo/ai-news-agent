# agents/agent2b_summarize_news.py

import anthropic
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from newspaper import Article

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    DATA_DIR, SCORING_MODEL, NEWS_SUMMARY_MAX_TOKENS, ARTICLE_WORD_LIMIT,
    GCP_PROJECT_ID, TOPIC_CONTENT_SUMMARIZED, FIRESTORE_COLLECTION, USE_FIRESTORE,
)

# --- Rate limiting ---
MAX_CONCURRENT_CLAUDE_CALLS = 3
MAX_FETCH_WORKERS = 20
_semaphore = threading.Semaphore(MAX_CONCURRENT_CLAUDE_CALLS)

FETCH_TIMEOUT    = 10
MIN_ARTICLE_WORDS = 100

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def fetch_article_text(url: str) -> str | None:
    try:
        article = Article(url, request_timeout=FETCH_TIMEOUT)
        article.config.browser_user_agent = USER_AGENT
        article.download()
        article.parse()
        text = article.text.strip()
        if not text:
            return None
        words = text.split()
        if len(words) < MIN_ARTICLE_WORDS:
            return None
        if len(words) > ARTICLE_WORD_LIMIT:
            words = words[:ARTICLE_WORD_LIMIT]
        return " ".join(words)
    except Exception:
        return None


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


def summarize_article(article: dict, text: str | None, prompt_template: str, fallback_template: str, client: anthropic.Anthropic) -> dict:
    title         = article.get("title", "")
    description   = article.get("description", "") or ""
    used_fallback = text is None

    if used_fallback:
        prompt = fallback_template.format(title=title, description=description)
    else:
        prompt = prompt_template.format(title=title, text=text)

    try:
        with _semaphore:
            response = claude_call_with_retry(
                client,
                model=SCORING_MODEL,
                max_tokens=NEWS_SUMMARY_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}]
            )
        summary = response.content[0].text.strip()
        return {**article, "summary": summary, "used_fallback": used_fallback, "summary_error": None}
    except Exception as e:
        return {**article, "summary": None, "used_fallback": used_fallback, "summary_error": str(e)}


def process_article(args: tuple) -> dict:
    client, article, prompt_template, fallback_template = args
    url  = article.get("url", "")
    text = fetch_article_text(url) if url else None
    return summarize_article(article, text, prompt_template, fallback_template, client)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def increment_and_check(run_id: str) -> bool:
    """
    Atomically increment agent2_completions in Firestore.
    Returns True if this agent is the one that pushed the count to 2,
    meaning both agent2a and agent2b are done and agent3 should be triggered.
    """
    from google.cloud import firestore

    db  = firestore.Client(project=GCP_PROJECT_ID)
    ref = db.collection(FIRESTORE_COLLECTION).document(run_id)

    ref.update({
        "agent2_completions": firestore.Increment(1)
    })

    updated = ref.get().to_dict()
    count   = updated.get("agent2_completions", 0)
    print(f"[agent2b]  agent2_completions = {count}")
    return count >= 2


def run(run_id: str):
    """Main agent logic. Called by main.py (Cloud Run) or orchestrator.py."""
    start_time = datetime.now()

    with open("prompts/news_summary_prompt.txt", "r", encoding="utf-8") as f:
        prompt_template = f.read()

    with open("prompts/news_summary_fallback_prompt.txt", "r", encoding="utf-8") as f:
        fallback_template = f.read()

    if USE_FIRESTORE:
        from google.cloud import firestore as _fs
        doc      = _fs.Client(project=GCP_PROJECT_ID).collection(FIRESTORE_COLLECTION).document(run_id).get().to_dict()
        filtered = doc["news_filtered"]
        print(f"[agent2b]  Loaded news_filtered from Firestore")
    else:
        in_path = os.path.join(DATA_DIR, "news_filtered.json")
        with open(in_path, "r", encoding="utf-8") as f:
            filtered = json.load(f)

    by_category: dict = filtered.get("by_category", {})
    all_articles: list = filtered.get("articles", [])

    print(f"Summarizing {len(all_articles)} articles across {len(by_category)} categories...\n")

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_1ST_API_KEY"))
    tasks  = [(client, article, prompt_template, fallback_template) for article in all_articles]

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
            publisher.publish(topic_path, data).result()
            print(f"[agent2b]  Both agent2s done — published to {TOPIC_CONTENT_SUMMARIZED} (run_id={run_id})")
        else:
            print(f"[agent2b]  Waiting for agent2a to finish before triggering agent3")


if __name__ == "__main__":
    run(run_id="local-debug")