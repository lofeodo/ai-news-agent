# agents/agent1b_news.py

import anthropic
import json
import os
import requests
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from filter_tool import FILTER_TOOL

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_DIR, SCORING_MODEL, MAX_TOKENS, NEWS_FETCH_SIZE, LOOKBACK_HOURS

# --- Constants ---
HN_TOP_STORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL        = "https://hacker-news.firebaseio.com/v0/item/{}.json"
NEWSAPI_URL        = "https://newsapi.org/v2/everything"
NEWSAPI_QUERY      = "artificial intelligence OR machine learning OR LLM OR deep learning"
HN_MAX_WORKERS     = 20

# --- Rate limiting (Claude calls) ---
MAX_CONCURRENT_CLAUDE_CALLS = 5
_semaphore = threading.Semaphore(MAX_CONCURRENT_CLAUDE_CALLS)


def fetch_hn_story(story_id: int, cutoff_timestamp: float) -> dict | None:
    """Fetch a single HN story by ID. Returns None if not a valid article or too old."""
    try:
        response = requests.get(HN_ITEM_URL.format(story_id), timeout=10)
        response.raise_for_status()
        item = response.json()

        # Only keep stories with a URL (skip Ask HN, Show HN text posts, etc.)
        if not item or item.get("type") != "story" or not item.get("url"):
            return None

        # Drop stories older than LOOKBACK_HOURS
        if item.get("time", 0) < cutoff_timestamp:
            return None

        return {
            "source":      "hackernews",
            "title":       item.get("title", ""),
            "description": "",           # HN has no description field
            "url":         item.get("url", ""),
            "score":       item.get("score", 0)
        }
    except Exception as e:
        print(f"  [hn error] story {story_id}: {e}")
        return None


def fetch_hn_articles() -> list:
    """Fetch top HN stories from the last LOOKBACK_HOURS, return up to NEWS_FETCH_SIZE."""
    print(f"Fetching Hacker News top stories (last {LOOKBACK_HOURS} hours)...")

    cutoff_timestamp = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).timestamp()

    response = requests.get(HN_TOP_STORIES_URL, timeout=10)
    response.raise_for_status()
    story_ids = response.json()[:NEWS_FETCH_SIZE * 3]   # fetch 3x and trim — many will be non-articles or too old

    print(f"  Fetching {len(story_ids)} story IDs in parallel...")
    articles = []

    with ThreadPoolExecutor(max_workers=HN_MAX_WORKERS) as executor:
        future_to_id = {executor.submit(fetch_hn_story, sid, cutoff_timestamp): sid for sid in story_ids}

        for future in as_completed(future_to_id):
            result = future.result()
            if result:
                articles.append(result)
            if len(articles) >= NEWS_FETCH_SIZE:
                break

    print(f"  Got {len(articles)} HN articles")
    return articles[:NEWS_FETCH_SIZE]


def fetch_newsapi_articles() -> list:
    """Fetch AI articles from NewsAPI.org from the last LOOKBACK_HOURS."""
    print(f"Fetching NewsAPI articles (last {LOOKBACK_HOURS} hours)...")

    api_key = os.environ.get("NEWS_API_KEY")
    if not api_key:
        raise EnvironmentError("NEWS_API_KEY environment variable not set")

    from_time = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")

    params = {
        "q":        NEWSAPI_QUERY,
        "language": "en",
        "sortBy":   "publishedAt",
        "pageSize": NEWS_FETCH_SIZE,
        "from":     from_time,
        "apiKey":   api_key
    }

    response = requests.get(NEWSAPI_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    print(f"  NewsAPI status: {data.get('status')}")
    print(f"  NewsAPI totalResults: {data.get('totalResults')}")
    print(f"  from_time used: {from_time}")

    articles = []
    for item in data.get("articles", []):
        articles.append({
            "source":      "newsapi",
            "title":       item.get("title", ""),
            "description": item.get("description", "") or "",
            "url":         item.get("url", ""),
            "score":       None
        })

    print(f"  Got {len(articles)} NewsAPI articles")
    return articles


def deduplicate(articles: list) -> list:
    """Remove duplicate articles by exact URL match."""
    seen_urls = set()
    deduped = []
    for article in articles:
        url = article["url"]
        if url and url not in seen_urls:
            seen_urls.add(url)
            deduped.append(article)

    removed = len(articles) - len(deduped)
    if removed:
        print(f"  Removed {removed} duplicate(s) by URL")
    return deduped


def format_articles_for_prompt(articles: list) -> str:
    """Format articles as a numbered list for the filter prompt."""
    lines = []
    for i, article in enumerate(articles):
        title = article["title"] or "(no title)"
        desc  = article["description"] or "(no description)"
        lines.append(f"[{i}] {title}\n    {desc}")
    return "\n\n".join(lines)


def filter_articles_with_claude(articles: list) -> list:
    """Use Claude to select the most AI-relevant articles. Returns filtered list."""
    print(f"\nFiltering {len(articles)} articles with Claude...")

    with open("prompts/news_filter_prompt.txt", "r", encoding="utf-8") as f:
        prompt_template = f.read()

    formatted = format_articles_for_prompt(articles)
    prompt = prompt_template.format(articles=formatted)

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_1ST_API_KEY"))

    with _semaphore:
        response = client.messages.create(
            model=SCORING_MODEL,
            max_tokens=MAX_TOKENS,
            tools=[FILTER_TOOL],
            tool_choice={"type": "tool", "name": "filter_articles"},
            messages=[{"role": "user", "content": prompt}]
        )

    selected_indices = response.content[0].input["selected_indices"]
    print(f"  Claude selected {len(selected_indices)} articles: indices {selected_indices}")

    filtered = []
    for i in selected_indices:
        if 0 <= i < len(articles):
            filtered.append(articles[i])
        else:
            print(f"  [warning] Claude returned out-of-range index {i}, skipping")

    return filtered


if __name__ == "__main__":
    start_time = datetime.now()

    # Fetch from both sources
    hn_articles   = fetch_hn_articles()
    news_articles = fetch_newsapi_articles()

    # Merge and deduplicate
    all_articles = hn_articles + news_articles
    print(f"\nMerged: {len(all_articles)} articles total")
    all_articles = deduplicate(all_articles)
    print(f"After dedup: {len(all_articles)} articles")

    # Filter with Claude
    filtered = filter_articles_with_claude(all_articles)

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n--- Done in {elapsed:.1f}s ---")
    print(f"Selected {len(filtered)} articles")

    print("\n=== FILTERED ARTICLES ===")
    for i, article in enumerate(filtered, 1):
        print(f"{i}. [{article['source']}] {article['title']}")
        print(f"   {article['url']}\n")

    # Save results
    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "news_filtered.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "run_at":          start_time.isoformat(),
            "elapsed_seconds": elapsed,
            "total_fetched":   len(all_articles),
            "total_selected":  len(filtered),
            "articles":        filtered
        }, f, indent=2, ensure_ascii=False)

    print(f"Saved results to {out_path}")