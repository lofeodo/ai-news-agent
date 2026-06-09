# agents/agent3_compose.py

import anthropic
import json
import os
import re
import sys
import time
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_DIR, SCORING_MODEL, GCP_PROJECT_ID, USE_FIRESTORE, FIRESTORE_COLLECTION, SEND_HOUR, SEND_WEEKDAY

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEWSLETTER_NAME = "Latent SpaceMail"
RECIPIENT_EMAIL = os.environ.get("NEWSLETTER_RECIPIENT_EMAIL", "")
SENDER_EMAIL    = "latentspacemail@gmail.com"
SENDER_NAME     = "Latent SpaceMail"

SENDGRID_SECRET_NAME = "sendgrid-api-key"
USE_SECRET_MANAGER   = os.environ.get("USE_SECRET_MANAGER", "false").lower() == "true"

ARTICLES_PER_CATEGORY_TARGET = "3 to 5"

NEWS_CATEGORIES = [
    "Model & Product Releases",
    "Industry & Business",
    "Policy, Law & Regulation",
    "Open Source & Tools",
    "Safety & Alignment",
    "Society & Culture",
    "Canada & Montreal",
]

SELECTION_MAX_TOKENS = 200
INTRO_MAX_TOKENS     = 300


# ---------------------------------------------------------------------------
# SendGrid
# ---------------------------------------------------------------------------

def _get_sendgrid_api_key() -> str:
    """Load SendGrid API key from Secret Manager (cloud) or env var (local)."""
    if USE_SECRET_MANAGER:
        from google.cloud import secretmanager
        client   = secretmanager.SecretManagerServiceClient()
        name     = f"projects/{GCP_PROJECT_ID}/secrets/{SENDGRID_SECRET_NAME}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8").strip()
    else:
        key = os.environ.get("SENDGRID_API_KEY", "")
        if not key:
            raise RuntimeError("SENDGRID_API_KEY env var not set for local mode")
        return key


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
# Article selection
# ---------------------------------------------------------------------------

def format_articles_for_selection(articles: list) -> str:
    lines = []
    for i, a in enumerate(articles):
        hn = f"hn_score: {a['hn_score']}" if a.get("hn_score") is not None else "hn_score: null"
        fallback_note = " [summary from description only]" if a.get("used_fallback") else ""
        lines.append(
            f"[{i}] {a.get('title', 'No title')}\n"
            f"    {hn}{fallback_note}\n"
            f"    Summary: {(a.get('summary') or a.get('description') or '')[:300]}"
        )
    return "\n\n".join(lines)


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


def select_articles_for_category(
    category: str,
    articles: list,
    prompt_template: str,
    client: anthropic.Anthropic,
) -> list[dict]:
    if not articles:
        return []

    formatted = format_articles_for_selection(articles)
    prompt    = prompt_template.format(category=category, articles=formatted)

    response = claude_call_with_retry(
        client,
        model=SCORING_MODEL,
        max_tokens=SELECTION_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )

    indices = parse_indices(response.content[0].text, len(articles))

    if not indices:
        print(f"  [warn]   no valid indices for '{category}' — falling back to first 3")
        indices = list(range(min(3, len(articles))))

    return [articles[i] for i in indices]


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
        f"- {p['title']} (score: {p['scores']['total']}/28)" for p in papers
    )

    headline_lines = []
    for cat, arts in selected_by_category.items():
        for a in arts[:2]:
            headline_lines.append(f"- [{cat}] {a.get('title', '')}")

    prompt = prompt_template.format(
        date=datetime.now().strftime("%B %d, %Y"),
        papers=paper_lines,
        headlines="\n".join(headline_lines[:15]),
    )

    response = claude_call_with_retry(
        client,
        model=SCORING_MODEL,
        max_tokens=INTRO_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# HTML composition
# ---------------------------------------------------------------------------

def render_paper_card(paper: dict) -> str:
    score   = paper["scores"]["total"]
    authors = ", ".join(paper.get("authors", [])[:3])
    if len(paper.get("authors", [])) > 3:
        authors += " et al."
    summary_paragraphs = [p.strip() for p in paper["summary"].split("\n\n") if p.strip()]
    summary_html = "".join(f"<p>{p}</p>" for p in summary_paragraphs)

    return f"""
    <div class="paper-card">
      <div class="paper-score">{score}/28</div>
      <h3 class="paper-title">
        <a href="{paper['pdf_url']}">{paper['title']}</a>
      </h3>
      <div class="paper-meta">{authors}</div>
      {summary_html}
    </div>"""


def render_article_card(article: dict) -> str:
    title   = article.get("title", "Untitled")
    url     = article.get("url", "#")
    summary = article.get("summary") or article.get("description") or ""
    hn      = article.get("hn_score")
    hn_badge = f'<span class="hn-badge">▲ {hn}</span>' if hn else ""

    return f"""
    <div class="article-card">
      <h4 class="article-title">
        <a href="{url}">{title}</a>{hn_badge}
      </h4>
      <p class="article-summary">{summary}</p>
    </div>"""


def compose_html(
    intro: str,
    papers: list,
    selected_by_category: dict[str, list],
    week_of: str,
) -> str:
    paper_cards = "\n".join(render_paper_card(p) for p in papers)
    research_section = f"""
    <div class="section">
      <h2 class="section-title">🔬 Research Spotlights</h2>
      {paper_cards}
    </div>"""

    news_sections = ""
    category_icons = {
        "Model & Product Releases": "🚀",
        "Industry & Business":      "💼",
        "Policy, Law & Regulation": "⚖️",
        "Open Source & Tools":      "🛠️",
        "Safety & Alignment":       "🛡️",
        "Society & Culture":        "🌍",
        "Canada & Montreal":        "🍁",
    }

    for category in NEWS_CATEGORIES:
        icon     = category_icons.get(category, "📌")
        articles = selected_by_category.get(category, [])

        if articles:
            body = "\n".join(render_article_card(a) for a in articles)
        else:
            body = '<p class="no-articles">No notable releases this week.</p>'

        news_sections += f"""
    <div class="section">
      <h2 class="section-title">{icon} {category}</h2>
      {body}
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{NEWSLETTER_NAME}</title>
  <style>
    body {{
      font-family: 'Menlo', 'Cascadia Code', 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
      background: #f4f4f4;
      color: #1a1a1a;
      margin: 0;
      padding: 0;
    }}
    .wrapper {{
      max-width: 680px;
      margin: 0 auto;
      background: #ffffff;
    }}
    .header {{
      background: #0f0f0f;
      color: #ffffff;
      padding: 32px 40px 24px;
    }}
    .header h1 {{
      margin: 0 0 6px 0;
      font-size: 28px;
      letter-spacing: 0.5px;
      font-family: 'Menlo', 'Cascadia Code', 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
      font-weight: 700;
    }}
    .header .subtitle {{
      font-size: 12px;
      color: #888888;
      font-family: 'Menlo', 'Cascadia Code', 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
      letter-spacing: 1px;
      text-transform: uppercase;
      margin: 0 0 10px 0;
    }}
    .header .week-label {{
      font-size: 13px;
      color: #aaaaaa;
      font-family: 'Menlo', 'Cascadia Code', 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    }}
    .intro {{
      padding: 28px 40px;
      font-size: 14px;
      line-height: 1.7;
      border-bottom: 1px solid #eeeeee;
      color: #333333;
      font-family: 'Menlo', 'Cascadia Code', 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    }}
    .section {{
      padding: 28px 40px;
      border-bottom: 1px solid #eeeeee;
    }}
    .section-title {{
      font-family: 'Helvetica Neue', Arial, sans-serif;
      font-size: 14px;
      font-weight: 700;
      letter-spacing: 1.5px;
      text-transform: uppercase;
      color: #888888;
      margin: 0 0 20px 0;
    }}
    .paper-card {{
      position: relative;
      margin-bottom: 28px;
      padding-left: 16px;
      border-left: 3px solid #0f0f0f;
    }}
    .paper-score {{
      position: absolute;
      top: 0;
      right: 0;
      background: #0f0f0f;
      color: #ffffff;
      font-family: 'Menlo', 'Cascadia Code', 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
      font-size: 11px;
      font-weight: 700;
      padding: 3px 7px;
      border-radius: 3px;
    }}
    .paper-title {{
      margin: 0 0 4px 0;
      font-size: 15px;
      line-height: 1.4;
      padding-right: 56px;
      font-family: 'Menlo', 'Cascadia Code', 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
      font-weight: 600;
    }}
    .paper-title a {{ color: #1a1a1a; text-decoration: none; }}
    .paper-title a:hover {{ text-decoration: underline; }}
    .paper-meta {{
      font-family: 'Menlo', 'Cascadia Code', 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
      font-size: 11px;
      color: #999999;
      margin-bottom: 10px;
    }}
    .paper-card p {{
      margin: 0 0 10px 0;
      font-size: 13px;
      line-height: 1.7;
      color: #444444;
      font-family: 'Menlo', 'Cascadia Code', 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    }}
    .article-card {{
      margin-bottom: 20px;
      padding-bottom: 20px;
      border-bottom: 1px solid #f0f0f0;
    }}
    .article-card:last-child {{ border-bottom: none; margin-bottom: 0; padding-bottom: 0; }}
    .article-title {{
      margin: 0 0 6px 0;
      font-size: 14px;
      font-family: 'Menlo', 'Cascadia Code', 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
      font-weight: 600;
    }}
    .article-title a {{ color: #1a1a1a; text-decoration: none; }}
    .article-title a:hover {{ text-decoration: underline; }}
    .hn-badge {{ margin-left: 8px; font-size: 11px; color: #ff6600; font-weight: 700; }}
    .article-summary {{
      margin: 0;
      font-size: 13px;
      line-height: 1.65;
      color: #555555;
      font-family: 'Menlo', 'Cascadia Code', 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    }}
    .no-articles {{
      font-size: 13px;
      color: #aaaaaa;
      font-style: italic;
      margin: 0;
      font-family: 'Menlo', 'Cascadia Code', 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
    }}
    .footer {{
      padding: 24px 40px;
      font-family: 'Menlo', 'Cascadia Code', 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
      font-size: 11px;
      color: #aaaaaa;
      text-align: center;
      background: #f9f9f9;
    }}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="header">
      <h1>{NEWSLETTER_NAME}</h1>
      <div class="subtitle">Your Weekly AI Briefing</div>
      <div class="week-label">Week of {week_of}</div>
    </div>
    <div class="intro">
      {intro}
    </div>
    {news_sections}
    {research_section}
    <div class="footer">
      You're receiving this because you set it up. Unsubscribe by turning off the Cloud Scheduler. ✌️
    </div>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# SendGrid send
# ---------------------------------------------------------------------------

def send_email(api_key: str, html_body: str, subject: str) -> None:
    import urllib.request
    payload = json.dumps({
        "personalizations": [{"to": [{"email": RECIPIENT_EMAIL}]}],
        "from":    {"email": SENDER_EMAIL, "name": SENDER_NAME},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_body}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        print(f"[sendgrid]  sent to {RECIPIENT_EMAIL} — status {resp.status}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(run_id: str):
    """Main agent logic. Called by main.py (Cloud Run) or orchestrator.py."""
    start_time = datetime.now()

    with open("prompts/article_selection_prompt.txt", "r", encoding="utf-8") as f:
        selection_prompt = f.read()
    with open("prompts/intro_prompt.txt", "r", encoding="utf-8") as f:
        intro_prompt = f.read()

    if USE_FIRESTORE:
        from google.cloud import firestore as _fs
        doc         = _fs.Client(project=GCP_PROJECT_ID).collection(FIRESTORE_COLLECTION).document(run_id).get().to_dict()
        papers      = doc["paper_summaries"]
        by_category = doc["news_summaries"]
        print(f"[agent3]  Loaded paper_summaries and news_summaries from Firestore")
    else:
        with open(os.path.join(DATA_DIR, "paper_summaries.json"), "r", encoding="utf-8") as f:
            paper_data = json.load(f)
        with open(os.path.join(DATA_DIR, "news_summaries.json"), "r", encoding="utf-8") as f:
            news_data = json.load(f)
        papers      = paper_data["papers"]
        by_category = news_data["by_category"]

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_1ST_API_KEY"))

    print("\n=== Selecting articles per category ===")
    selected_by_category: dict[str, list] = {}

    for category in NEWS_CATEGORIES:
        articles = by_category.get(category, [])
        print(f"  [{category}] {len(articles)} articles available", end="")

        if not articles:
            print(" → no articles, using fallback")
            selected_by_category[category] = []
            continue

        selected = select_articles_for_category(category, articles, selection_prompt, client)
        print(f" → selected {len(selected)}")
        selected_by_category[category] = selected

    print("\n=== Writing intro paragraph ===")
    intro = write_intro(papers, selected_by_category, intro_prompt, client)
    print(f"  Intro: {intro[:100]}...")

    print("\n=== Composing HTML ===")
    week_of = datetime.now().strftime("%B %d, %Y")
    html    = compose_html(intro, papers, selected_by_category, week_of)

    os.makedirs(DATA_DIR, exist_ok=True)
    html_path = os.path.join(DATA_DIR, "newsletter.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Saved to {html_path}")

    print("\n=== Sending email ===")
    if not RECIPIENT_EMAIL:
        print("  [warn]  NEWSLETTER_RECIPIENT_EMAIL not set — skipping send")
    else:
        subject = f"{NEWSLETTER_NAME} — {week_of}"
        api_key = _get_sendgrid_api_key()

        # Wait until SEND_HOUR on SEND_WEEKDAY if we're running ahead of schedule.
        from zoneinfo import ZoneInfo
        eastern = ZoneInfo("America/Toronto")
        now_eastern = datetime.now(eastern)
        target = now_eastern.replace(hour=SEND_HOUR, minute=0, second=0, microsecond=0)
        if now_eastern < target:
            wait_seconds = (target - now_eastern).total_seconds()
            print(f"  [scheduler]  pipeline finished early — sleeping {wait_seconds:.0f}s until {SEND_HOUR}:00 Eastern")
            time.sleep(wait_seconds)
        else:
            print(f"  [scheduler]  past {SEND_HOUR}:00 Eastern — sending immediately")

        send_email(api_key, html, subject)

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n--- Done in {elapsed:.1f}s ---")

    total_selected = sum(len(v) for v in selected_by_category.values())
    print(f"Papers:   {len(papers)}")
    print(f"Articles: {total_selected} selected across {len(NEWS_CATEGORIES)} categories")
    print(f"HTML:     {html_path}")


if __name__ == "__main__":
    run(run_id="local-debug")