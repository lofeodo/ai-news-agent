# agents/agent3_compose.py

import anthropic
import json
import os
import re
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_DIR, SCORING_MODEL, GCP_PROJECT_ID, USE_FIRESTORE, FIRESTORE_COLLECTION

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEWSLETTER_NAME              = "Latent SpaceMail"
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
# HTML composition — table-based, all-inline styles (Gmail/Outlook safe)
# ---------------------------------------------------------------------------

_F     = "'Menlo','Cascadia Mono','Consolas','Courier New',monospace"
_AMBER = "#c8b89a"
_DARK  = "#0f0f0f"
_WHITE = "#ffffff"
_TEXT  = "#1a1a1a"
_DIM   = "#555555"
_MUTED = "#888888"
_FAINT = "#aaaaaa"
_SEPR  = "#e8e8e8"
_WARM  = "#f5f3ec"
_HN    = "#ff6600"


def render_paper_card(paper: dict) -> str:
    score        = paper["scores"]["total"]
    authors_list = paper.get("authors", [])
    authors      = ", ".join(authors_list[:3]) + (" et al." if len(authors_list) > 3 else "")
    paragraphs   = [p.strip() for p in paper["summary"].split("\n\n") if p.strip()]

    summary_rows = "".join(
        f'<tr><td style="padding:{"0" if i == 0 else "10px"} 0 0 0;'
        f'font-family:{_F};font-size:14px;line-height:1.78;color:{_DIM};">{para}</td></tr>\n'
        for i, para in enumerate(paragraphs)
    )

    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"'
        f' style="margin-bottom:32px;border-left:3px solid {_AMBER};">\n'
        f'<tr><td style="padding:16px 20px 20px;background:{_WARM};">\n'
        # title row + score badge
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">\n'
        f'<tr>\n'
        f'<td style="vertical-align:top;padding-right:10px;">'
        f'<p style="margin:0 0 5px 0;font-family:{_F};font-size:15px;font-weight:600;line-height:1.4;">'
        f'<a href="{paper["pdf_url"]}" style="color:{_TEXT};text-decoration:none;">{paper["title"]}</a>'
        f'</p></td>\n'
        f'<td width="58" valign="top" style="white-space:nowrap;">'
        f'<span style="background:{_DARK};color:{_AMBER};font-family:{_F};'
        f'font-size:11px;font-weight:700;padding:3px 7px;display:inline-block;">{score}/28</span>'
        f'</td>\n'
        f'</tr>\n'
        f'</table>\n'
        # authors
        f'<p style="margin:0 0 12px 0;font-family:{_F};font-size:11px;color:{_MUTED};">{authors}</p>\n'
        # summary
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">\n'
        f'{summary_rows}'
        f'</table>\n'
        # read paper CTA
        f'<p style="margin:14px 0 0 0;font-family:{_F};font-size:12px;">'
        f'<a href="{paper["pdf_url"]}" style="color:{_AMBER};text-decoration:none;">Read paper &#8594;</a>'
        f'</p>\n'
        f'</td></tr>\n'
        f'</table>\n'
    )


def render_article_card(article: dict, is_last: bool = False) -> str:
    title   = article.get("title", "Untitled")
    url     = article.get("url", "#")
    summary = article.get("summary") or article.get("description") or ""
    hn      = article.get("hn_score")

    hn_html = (
        f'&nbsp;&nbsp;<span style="font-size:12px;color:{_HN};font-weight:700;">&#9650;&nbsp;{hn}</span>'
        if hn else ""
    )
    sep   = "" if is_last else f"padding-bottom:20px;border-bottom:1px solid {_SEPR};"
    mb    = "0" if is_last else "20px"

    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"'
        f' style="margin-bottom:{mb};{sep}">\n'
        f'<tr><td>'
        f'<p style="margin:0 0 7px 0;font-family:{_F};font-size:15px;font-weight:600;line-height:1.4;">'
        f'<a href="{url}" style="color:{_TEXT};text-decoration:none;">{title}</a>{hn_html}</p>\n'
        f'<p style="margin:0;font-family:{_F};font-size:14px;line-height:1.75;color:{_DIM};">{summary}</p>'
        f'</td></tr>\n'
        f'</table>\n'
    )


def compose_html(
    intro: str,
    papers: list,
    selected_by_category: dict[str, list],
    week_of: str,
) -> str:
    category_icons = {
        "Model & Product Releases": "🚀",
        "Industry & Business":      "💼",
        "Policy, Law & Regulation": "⚖️",
        "Open Source & Tools":      "🛠️",
        "Safety & Alignment":       "🛡️",
        "Society & Culture":        "🌍",
        "Canada & Montreal":        "🍁",
    }

    # CASL: physical mailing address sourced from env var (satisfies CASL when set)
    _mailing_address = os.environ.get("MAILING_ADDRESS", "").strip()
    address_html = (
        f'<p style="margin:0 0 10px 0;font-family:{_F};font-size:12px;color:{_MUTED};">'
        f'{_mailing_address}</p>'
        if _mailing_address else ""
    )

    # --- section heading HTML helper ---
    def _section_heading(icon: str, label: str) -> str:
        return (
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"'
            f' style="margin-bottom:20px;">\n'
            f'<tr><td style="padding-bottom:10px;border-bottom:1px solid {_SEPR};">'
            f'<p style="margin:0;font-family:{_F};font-size:11px;font-weight:700;'
            f'letter-spacing:2.5px;text-transform:uppercase;color:{_AMBER};">'
            f'{icon}&nbsp; {label}</p>'
            f'</td></tr>\n'
            f'</table>\n'
        )

    # --- news section rows ---
    news_rows = ""
    for category in NEWS_CATEGORIES:
        icon     = category_icons.get(category, "📌")
        articles = selected_by_category.get(category, [])

        if articles:
            body = "".join(
                render_article_card(a, is_last=(i == len(articles) - 1))
                for i, a in enumerate(articles)
            )
        else:
            body = (
                f'<p style="font-family:{_F};font-size:13px;color:{_FAINT};'
                f'font-style:italic;margin:0;">No notable releases this week.</p>'
            )

        news_rows += (
            f'<tr><td style="padding:28px 40px;border-bottom:1px solid {_SEPR};background:{_WHITE};">\n'
            f'{_section_heading(icon, category)}'
            f'{body}'
            f'</td></tr>\n'
        )

    # --- research section row ---
    paper_cards  = "".join(render_paper_card(p) for p in papers)
    research_row = (
        f'<tr><td style="padding:28px 40px;border-bottom:1px solid {_SEPR};background:{_WHITE};">\n'
        f'{_section_heading("🔬", "Research Spotlights")}'
        f'{paper_cards}'
        f'</td></tr>\n'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>{NEWSLETTER_NAME}</title>
  <!--[if mso]><xml><o:OfficeDocumentSettings><o:AllowPNG/><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]-->
  <style>
    /* Apple Mail / iOS dark-mode overrides only — not relied on for layout */
    @media (prefers-color-scheme: dark) {{
      .dm-bg   {{ background:#111111 !important; }}
      .dm-card {{ background:#1a1a1a !important; border-color:#2a2a2a !important; }}
      .dm-text {{ color:#e0e0e0 !important; }}
      .dm-warm {{ background:#1e1c19 !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:#e9e9e9;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">

<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
<tr><td class="dm-bg" style="padding:28px 12px;background:#e9e9e9;">

  <!-- email wrapper: max 640px, amber top accent -->
  <table role="presentation" class="dm-card" width="100%" cellspacing="0" cellpadding="0" border="0"
         style="max-width:640px;margin:0 auto;background:{_WHITE};
                border:1px solid #d5d5d5;border-top:3px solid {_AMBER};">

    <!-- ╔══════════════════ HEADER ══════════════════╗ -->
    <tr>
      <td style="background:{_DARK};padding:36px 40px 28px;">
        <p style="margin:0 0 6px 0;font-family:{_F};font-size:13px;color:#3a3a3a;letter-spacing:1px;">&gt;_</p>
        <h1 style="margin:0 0 8px 0;font-family:{_F};font-size:26px;font-weight:700;
                   letter-spacing:0.5px;color:{_AMBER};">{NEWSLETTER_NAME}</h1>
        <p style="margin:0 0 6px 0;font-family:{_F};font-size:11px;color:#555555;
                  letter-spacing:2.5px;text-transform:uppercase;">Your Weekly AI Briefing</p>
        <p style="margin:0;font-family:{_F};font-size:13px;color:#777777;">Week of {week_of}</p>
      </td>
    </tr>

    <!-- ╔══════════════════ INTRO ══════════════════╗ -->
    <tr>
      <td class="dm-text" style="padding:28px 40px 24px;border-bottom:1px solid {_SEPR};background:{_WHITE};">
        <p style="margin:0;font-family:{_F};font-size:15px;line-height:1.82;color:{_TEXT};">{intro}</p>
      </td>
    </tr>

    {news_rows}
    {research_row}

    <!-- ╔══════════════════ FOOTER ══════════════════╗ -->
    <tr>
      <td style="background:{_DARK};padding:28px 40px;text-align:center;">
        <p style="margin:0 0 6px 0;font-family:{_F};font-size:13px;color:{_MUTED};">{NEWSLETTER_NAME} — Your Weekly AI Briefing</p>
        <p style="margin:0 0 14px 0;font-family:{_F};font-size:12px;color:{_DIM};">You're receiving this because you subscribed at {NEWSLETTER_NAME}.</p>
        {address_html}
        <p style="margin:0;font-family:{_F};font-size:12px;">
          <a href="{{{{UNSUBSCRIBE_URL}}}}" style="color:{_MUTED};text-decoration:underline;">Unsubscribe</a>
          <span style="color:#333333;">&nbsp;&nbsp;·&nbsp;&nbsp;</span>
          <a href="{{{{PREFERENCES_URL}}}}" style="color:{_MUTED};text-decoration:underline;">Manage preferences</a>
        </p>
      </td>
    </tr>

  </table>

</td></tr>
</table>
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

    print("\n=== Saving newsletter HTML ===")
    os.makedirs(DATA_DIR, exist_ok=True)
    html_path = os.path.join(DATA_DIR, "newsletter.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Saved to {html_path}")

    if USE_FIRESTORE:
        from google.cloud import firestore as _fs
        _fs.Client(project=GCP_PROJECT_ID).collection(FIRESTORE_COLLECTION).document(run_id).update({
            "newsletter_html":    html,
            "newsletter_subject": f"{NEWSLETTER_NAME} — {week_of}",
        })
        print(f"  Written newsletter_html and newsletter_subject to Firestore")

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n--- Done in {elapsed:.1f}s ---")

    total_selected = sum(len(v) for v in selected_by_category.values())
    print(f"Papers:   {len(papers)}")
    print(f"Articles: {total_selected} selected across {len(NEWS_CATEGORIES)} categories")
    print(f"HTML:     {html_path}")


if __name__ == "__main__":
    run(run_id="local-debug")