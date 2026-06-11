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
# Design: dark editorial dispatch — industrial zine meets hacker terminal
# ---------------------------------------------------------------------------

_F     = "'Menlo','Cascadia Mono','Consolas','Courier New',monospace"
# Darks
_D0    = "#080808"   # header / footer (deepest)
_D1    = "#111111"   # page outer bg / research section
_D2    = "#191919"   # section number strips
_D3    = "#222222"   # paper card bg
# Lights
_WHITE = "#ffffff"   # article content
_CREAM = "#faf9f4"   # intro (barely warm)
# Text
_INK   = "#111111"   # body text on white
_INK2  = "#555555"   # secondary text on white
_ASH   = "#d4cfc8"   # text on dark
_CHAR  = "#6a6560"   # secondary text on dark
# Accents
_AMBER = "#c8b89a"   # brand amber (soft, warm)
_GOLD  = "#d4a843"   # bright gold for score numbers
_HN    = "#ff6600"   # Hacker News orange
_SEPR  = "#ededed"   # separator on white


def render_paper_card(paper: dict) -> str:
    score        = paper["scores"]["total"]
    authors_list = paper.get("authors", [])
    authors      = ", ".join(authors_list[:3]) + (" et al." if len(authors_list) > 3 else "")
    paragraphs   = [p.strip() for p in paper["summary"].split("\n\n") if p.strip()]

    summary_rows = "".join(
        f'<tr><td style="padding:{"0" if i == 0 else "10px"} 0 0 0;'
        f'font-family:{_F};font-size:14px;line-height:1.82;color:#8a8580;">{para}</td></tr>\n'
        for i, para in enumerate(paragraphs)
    )

    return (
        # dark card, 2px gap between cards via margin-bottom
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"'
        f' style="margin-bottom:3px;">\n'
        f'<tr><td style="padding:22px 26px 22px;background:{_D3};">\n'
        # title (left) + large score (right)
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">\n'
        f'<tr>\n'
        f'<td style="vertical-align:top;padding-right:18px;">'
        f'<p style="margin:0 0 6px 0;font-family:{_F};font-size:15px;font-weight:600;line-height:1.38;">'
        f'<a href="{paper["pdf_url"]}" style="color:{_ASH};text-decoration:none;">{paper["title"]}'
        f'&nbsp;<span style="font-size:11px;color:{_AMBER};font-weight:400;">&#x2197;</span></a>'
        f'</p></td>\n'
        f'<td width="60" valign="top" style="white-space:nowrap;text-align:right;">'
        f'<p style="margin:0;font-family:{_F};line-height:1;">'
        f'<span style="font-size:26px;font-weight:700;color:{_GOLD};">{score}</span>'
        f'<br><span style="font-size:10px;color:{_AMBER};letter-spacing:1px;">/28</span>'
        f'</p></td>\n'
        f'</tr>\n'
        f'</table>\n'
        f'<p style="margin:0 0 14px 0;font-family:{_F};font-size:11px;color:{_CHAR};">{authors}</p>\n'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">\n'
        f'{summary_rows}'
        f'</table>\n'
        f'<p style="margin:16px 0 0 0;font-family:{_F};font-size:12px;">'
        f'<a href="{paper["pdf_url"]}" style="color:{_AMBER};text-decoration:none;">'
        f'Read paper &nbsp;&#8594;</a>'
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
        f'&nbsp;&nbsp;<span style="font-size:11px;color:{_HN};font-weight:700;'
        f'letter-spacing:0.5px;">&#9650;&nbsp;{hn}</span>'
        if hn else ""
    )
    sep = "" if is_last else f"padding-bottom:24px;border-bottom:1px solid {_SEPR};"
    mb  = "0"  if is_last else "24px"

    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"'
        f' style="margin-bottom:{mb};{sep}">\n'
        f'<tr><td>'
        f'<p style="margin:0 0 8px 0;font-family:{_F};font-size:16px;font-weight:600;line-height:1.35;">'
        f'<a href="{url}" style="color:{_INK};text-decoration:none;">{title}'
        f'&nbsp;<span style="font-size:11px;color:{_AMBER};font-weight:400;">&#x2197;</span></a>{hn_html}</p>\n'
        f'<p style="margin:0;font-family:{_F};font-size:14px;line-height:1.8;color:{_INK2};">{summary}</p>'
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
        f'<p style="margin:0 0 10px 0;font-family:{_F};font-size:12px;color:#3a3a3a;">'
        f'{_mailing_address}</p>'
        if _mailing_address else ""
    )

    # — thin horizontal rule helper (1px, given color) —
    def _rule(color: str, my: str = "0") -> str:
        return (
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"'
            f' style="margin:{my} 0;">'
            f'<tr><td height="1" style="background:{color};font-size:0;line-height:0;">&nbsp;</td></tr>'
            f'</table>\n'
        )

    # — dark section-number strip (editorial zine style) —
    def _section_strip(num: str, icon: str, label: str) -> str:
        return (
            f'<tr><td style="background:{_D2};padding:13px 40px;">'
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">\n'
            f'<tr>\n'
            # large section number
            f'<td style="vertical-align:middle;white-space:nowrap;padding-right:16px;">'
            f'<span style="font-family:{_F};font-size:28px;font-weight:700;color:{_AMBER};'
            f'line-height:1;letter-spacing:-1px;">{num}</span>'
            f'</td>\n'
            # vertical divider + label
            f'<td style="vertical-align:middle;border-left:1px solid #333333;padding-left:16px;">'
            f'<p style="margin:0;font-family:{_F};font-size:10px;font-weight:700;'
            f'letter-spacing:3px;text-transform:uppercase;color:#4a4a4a;">'
            f'{icon}&nbsp; {label}</p>'
            f'</td>\n'
            f'</tr>\n'
            f'</table>\n'
            f'</td></tr>\n'
        )

    # — table of contents (2 rows × 4 cols) —
    _toc_labels = [
        ("01", cat.split(" &")[0].split(",")[0].split(" ")[0].upper())
        for cat in NEWS_CATEGORIES
    ]
    _toc_labels.append(("08", "RESEARCH"))

    def _toc_cell(num: str, short: str) -> str:
        return (
            f'<td style="width:25%;padding:0 0 6px 0;">'
            f'<span style="font-family:{_F};font-size:9px;color:{_AMBER};font-weight:700;">{num}</span>'
            f'<span style="font-family:{_F};font-size:9px;color:#363636;">&thinsp;{short}</span>'
            f'</td>'
        )

    toc_row1 = "".join(_toc_cell(n, s) for n, s in _toc_labels[:4])
    toc_row2 = "".join(_toc_cell(n, s) for n, s in _toc_labels[4:])

    # fix section numbers in toc since we used enumerate index above
    _toc_labels_fixed = [(f"{i+1:02d}", cat.split(" &")[0].split(",")[0].upper())
                         for i, cat in enumerate(NEWS_CATEGORIES)]
    _toc_labels_fixed.append(("RES", "RESEARCH"))
    toc_row1 = "".join(_toc_cell(n, s) for n, s in _toc_labels_fixed[:4])
    toc_row2 = "".join(_toc_cell(n, s) for n, s in _toc_labels_fixed[4:])

    # — news section rows —
    news_rows = ""
    for i, category in enumerate(NEWS_CATEGORIES):
        num      = f"{i+1:02d}"
        icon     = category_icons.get(category, "📌")
        articles = selected_by_category.get(category, [])

        news_rows += _section_strip(num, icon, category)

        if articles:
            body = "".join(
                render_article_card(a, is_last=(j == len(articles) - 1))
                for j, a in enumerate(articles)
            )
        else:
            body = (
                f'<p style="font-family:{_F};font-size:13px;color:#aaaaaa;'
                f'font-style:italic;margin:0;">No notable releases this week.</p>'
            )

        news_rows += (
            f'<tr><td style="background:{_WHITE};padding:28px 40px;">\n'
            f'{body}\n'
            f'</td></tr>\n'
        )

    # — research section —
    paper_cards   = "".join(render_paper_card(p) for p in papers)
    research_rows = (
        _section_strip("RES", "🔬", "Research Spotlights") +
        f'<tr><td style="background:{_D1};padding:20px 40px 32px;">\n'
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
    @media (prefers-color-scheme: dark) {{
      .dm-outer {{ background:#060606 !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:{_D1};-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">

<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
<tr><td class="dm-outer" style="padding:32px 12px 48px;background:{_D1};">

  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
         style="max-width:640px;margin:0 auto;">

    <!-- ════════════════════════════════════════════
         HEADER
         ════════════════════════════════════════════ -->
    <tr><td style="background:{_D0};">
      <!-- amber top accent bar -->
      {_rule(_AMBER)}
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr><td style="padding:32px 40px 24px;">
        <p style="margin:0 0 4px 0;font-family:{_F};font-size:11px;color:#2c2c2c;letter-spacing:3px;">&gt;_ TRANSMISSION</p>
        <h1 style="margin:0 0 0 0;font-family:{_F};font-size:32px;font-weight:700;
                   letter-spacing:-0.5px;line-height:1.1;color:{_AMBER};">{NEWSLETTER_NAME}</h1>
        {_rule("#222222", "14px")}
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
               style="margin-top:12px;">
        <tr>
          <td style="vertical-align:bottom;">
            <p style="margin:0;font-family:{_F};font-size:10px;color:#404040;
                      letter-spacing:3px;text-transform:uppercase;">
              Weekly AI Intelligence Dispatch
            </p>
          </td>
          <td style="vertical-align:bottom;text-align:right;white-space:nowrap;">
            <p style="margin:0;font-family:{_F};font-size:11px;color:#383838;">
              {week_of}
            </p>
          </td>
        </tr>
        </table>
      </td></tr>
      </table>

      <!-- contents index -->
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
             style="border-top:1px solid #181818;">
      <tr><td style="padding:12px 40px 14px;background:#0d0d0d;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
          <tr>{toc_row1}</tr>
          <tr>{toc_row2}</tr>
        </table>
      </td></tr>
      </table>
    </td></tr>

    <!-- ════════════════════════════════════════════
         INTRO
         ════════════════════════════════════════════ -->
    <tr><td style="background:{_CREAM};padding:28px 40px 26px;border-bottom:2px solid {_D2};">
      <p style="margin:0 0 0 0;font-family:{_F};font-size:15px;line-height:1.88;color:{_INK};">{intro}</p>
    </td></tr>

    {news_rows}
    {research_rows}

    <!-- ════════════════════════════════════════════
         FOOTER
         ════════════════════════════════════════════ -->
    <tr><td style="background:{_D0};padding:0;">
      {_rule("#1a1a1a")}
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr><td style="padding:24px 40px;text-align:center;">
        <p style="margin:0 0 4px 0;font-family:{_F};font-size:11px;color:#2e2e2e;
                  letter-spacing:3px;text-transform:uppercase;">{NEWSLETTER_NAME}</p>
        <p style="margin:0 0 14px 0;font-family:{_F};font-size:12px;color:#2e2e2e;">
          You're receiving this because you subscribed.
        </p>
        {address_html}
        <p style="margin:0;font-family:{_F};font-size:12px;">
          <a href="{{{{UNSUBSCRIBE_URL}}}}" style="color:#484848;text-decoration:underline;">Unsubscribe</a>
          <span style="color:#242424;">&nbsp;&nbsp;·&nbsp;&nbsp;</span>
          <a href="{{{{PREFERENCES_URL}}}}" style="color:#484848;text-decoration:underline;">Manage preferences</a>
        </p>
      </td></tr>
      </table>
    </td></tr>

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