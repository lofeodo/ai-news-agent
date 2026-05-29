# agents/agent3_compose.py

import anthropic
import base64
import json
import os
import re
import sys
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_DIR, SCORING_MODEL

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEWSLETTER_NAME = "Latent Spacemail"
RECIPIENT_EMAIL = os.environ.get("NEWSLETTER_RECIPIENT_EMAIL", "")
SENDER_EMAIL    = "latentspacemail@gmail.com"

GMAIL_SCOPES        = ["https://www.googleapis.com/auth/gmail.send"]
CREDENTIALS_PATH    = "credentials.json"
TOKEN_PATH          = "token.json"

# How many articles to show Claude as candidates per category
# (all articles are sent; this is just a note for prompt clarity)
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

SELECTION_MAX_TOKENS = 200   # just a JSON array of indices
INTRO_MAX_TOKENS     = 300   # 3-5 sentences


# ---------------------------------------------------------------------------
# Gmail auth
# ---------------------------------------------------------------------------

def get_gmail_service():
    """Authenticate via OAuth 2.0 and return a Gmail API service object.
    
    On first run: opens a browser window for you to log in and authorize.
    On subsequent runs: loads the saved token.json silently.
    """
    creds = None

    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[gmail]  refreshing expired token...")
            creds.refresh(Request())
        else:
            print("[gmail]  no valid token found — opening browser for OAuth login...")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        print("[gmail]  token saved to token.json")

    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# Claude helpers
# ---------------------------------------------------------------------------

def claude_call_with_retry(client: anthropic.Anthropic, max_retries: int = 4, **kwargs) -> object:
    """Call client.messages.create with exponential backoff on rate limit errors."""
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
    """Format article list as a numbered block for the selection prompt."""
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
    """Parse a JSON array of integers from Claude's response. Filters out-of-range indices."""
    try:
        # Strip any accidental markdown fences
        clean = re.sub(r"```[a-z]*", "", response_text).strip()
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
    """Ask Claude to pick the best 3-5 articles for a category. Returns selected article dicts."""

    if not articles:
        return []

    formatted = format_articles_for_selection(articles)
    prompt = prompt_template.format(category=category, articles=formatted)

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
    """Ask Claude to write a dynamic intro paragraph referencing actual content."""

    paper_lines = "\n".join(
        f"- {p['title']} (score: {p['scores']['total']}/28)" for p in papers
    )

    headline_lines = []
    for cat, arts in selected_by_category.items():
        for a in arts[:2]:  # just top 2 per category as context
            headline_lines.append(f"- [{cat}] {a.get('title', '')}")

    prompt = prompt_template.format(
        date=datetime.now().strftime("%B %d, %Y"),
        papers=paper_lines,
        headlines="\n".join(headline_lines[:15]),  # cap at 15 headlines
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
    score = paper["scores"]["total"]
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
    source  = article.get("source", "")
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
    """Assemble the full HTML email."""

    # --- Research Spotlights ---
    paper_cards = "\n".join(render_paper_card(p) for p in papers)
    research_section = f"""
    <div class="section">
      <h2 class="section-title">🔬 Research Spotlights</h2>
      {paper_cards}
    </div>"""

    # --- News sections ---
    news_sections = ""
    category_icons = {
        "Model & Product Releases":  "🚀",
        "Industry & Business":       "💼",
        "Policy, Law & Regulation":  "⚖️",
        "Open Source & Tools":       "🛠️",
        "Safety & Alignment":        "🛡️",
        "Society & Culture":         "🌍",
        "Canada & Montreal":         "🍁",
    }

    for category in NEWS_CATEGORIES:
        icon = category_icons.get(category, "📌")
        articles = selected_by_category.get(category, [])

        if articles:
            article_cards = "\n".join(render_article_card(a) for a in articles)
            body = article_cards
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
      font-family: Georgia, 'Times New Roman', serif;
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
      margin: 0 0 4px 0;
      font-size: 28px;
      letter-spacing: 0.5px;
    }}
    .header .week-label {{
      font-size: 13px;
      color: #aaaaaa;
      font-family: 'Helvetica Neue', Arial, sans-serif;
    }}
    .intro {{
      padding: 28px 40px;
      font-size: 16px;
      line-height: 1.7;
      border-bottom: 1px solid #eeeeee;
      color: #333333;
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
      font-family: 'Helvetica Neue', Arial, sans-serif;
      font-size: 11px;
      font-weight: 700;
      padding: 3px 7px;
      border-radius: 3px;
    }}
    .paper-title {{
      margin: 0 0 4px 0;
      font-size: 16px;
      line-height: 1.4;
      padding-right: 56px;
    }}
    .paper-title a {{
      color: #1a1a1a;
      text-decoration: none;
    }}
    .paper-title a:hover {{
      text-decoration: underline;
    }}
    .paper-meta {{
      font-family: 'Helvetica Neue', Arial, sans-serif;
      font-size: 12px;
      color: #999999;
      margin-bottom: 10px;
    }}
    .paper-card p {{
      margin: 0 0 10px 0;
      font-size: 14px;
      line-height: 1.65;
      color: #444444;
    }}
    .article-card {{
      margin-bottom: 20px;
      padding-bottom: 20px;
      border-bottom: 1px solid #f0f0f0;
    }}
    .article-card:last-child {{
      border-bottom: none;
      margin-bottom: 0;
      padding-bottom: 0;
    }}
    .article-title {{
      margin: 0 0 6px 0;
      font-size: 15px;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      font-weight: 600;
    }}
    .article-title a {{
      color: #1a1a1a;
      text-decoration: none;
    }}
    .article-title a:hover {{
      text-decoration: underline;
    }}
    .hn-badge {{
      margin-left: 8px;
      font-size: 11px;
      color: #ff6600;
      font-weight: 700;
    }}
    .article-summary {{
      margin: 0;
      font-size: 14px;
      line-height: 1.6;
      color: #555555;
    }}
    .no-articles {{
      font-size: 14px;
      color: #aaaaaa;
      font-style: italic;
      margin: 0;
    }}
    .footer {{
      padding: 24px 40px;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      font-size: 12px;
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
      <div class="week-label">Week of {week_of}</div>
    </div>
    <div class="intro">
      {intro}
    </div>
    {research_section}
    {news_sections}
    <div class="footer">
      You're receiving this because you set it up. Unsubscribe by turning off the Cloud Scheduler. ✌️
    </div>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Gmail send
# ---------------------------------------------------------------------------

def send_email(service, html_body: str, subject: str) -> None:
    """Send the newsletter HTML via Gmail API."""
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"]    = SENDER_EMAIL
    message["To"]      = RECIPIENT_EMAIL

    message.attach(MIMEText(html_body, "html"))

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"[gmail]  sent to {RECIPIENT_EMAIL}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    start_time = datetime.now()

    # --- Load prompts ---
    with open("prompts/article_selection_prompt.txt", "r", encoding="utf-8") as f:
        selection_prompt = f.read()
    with open("prompts/intro_prompt.txt", "r", encoding="utf-8") as f:
        intro_prompt = f.read()

    # --- Load data ---
    with open(os.path.join(DATA_DIR, "paper_summaries.json"), "r", encoding="utf-8") as f:
        paper_data = json.load(f)
    with open(os.path.join(DATA_DIR, "news_summaries.json"), "r", encoding="utf-8") as f:
        news_data = json.load(f)

    papers      = paper_data["papers"]
    by_category = news_data["by_category"]

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_1ST_API_KEY"))

    # --- Step 1: Select articles per category ---
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

    # --- Step 2: Write intro ---
    print("\n=== Writing intro paragraph ===")
    intro = write_intro(papers, selected_by_category, intro_prompt, client)
    print(f"  Intro: {intro[:100]}...")

    # --- Step 3: Compose HTML ---
    print("\n=== Composing HTML ===")
    week_of = datetime.now().strftime("%B %d, %Y")
    html = compose_html(intro, papers, selected_by_category, week_of)

    # Save local copy
    os.makedirs(DATA_DIR, exist_ok=True)
    html_path = os.path.join(DATA_DIR, "newsletter.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Saved to {html_path}")

    # --- Step 4: Send via Gmail ---
    print("\n=== Sending email ===")
    if not RECIPIENT_EMAIL:
        print("  [warn]  NEWSLETTER_RECIPIENT_EMAIL not set — skipping send")
        print("  Set it with: set NEWSLETTER_RECIPIENT_EMAIL=you@example.com")
    else:
        subject = f"{NEWSLETTER_NAME} — {week_of}"
        gmail_service = get_gmail_service()
        send_email(gmail_service, html, subject)

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n--- Done in {elapsed:.1f}s ---")

    # --- Summary ---
    total_selected = sum(len(v) for v in selected_by_category.values())
    print(f"Papers:   {len(papers)}")
    print(f"Articles: {total_selected} selected across {len(NEWS_CATEGORIES)} categories")
    print(f"HTML:     {html_path}")