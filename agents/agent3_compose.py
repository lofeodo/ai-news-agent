# agents/agent3_compose.py

import anthropic
import html as _html
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompt_guard import GUARD_XML_TAGS, neutralize_tags
import tracing
from config import DATA_DIR, SCORING_MODEL, GCP_PROJECT_ID, USE_FIRESTORE, FIRESTORE_COLLECTION, newsletter_send_date

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

NEWSLETTER_VARIANTS = {
    "0_0": {"include_french": False, "include_canada": False},
    "1_0": {"include_french": True,  "include_canada": False},
    "0_1": {"include_french": False, "include_canada": True},
    "1_1": {"include_french": True,  "include_canada": True},
}

SELECTION_MAX_TOKENS = 200
INTRO_MAX_TOKENS     = 300

_PROMPT_INJECTION_GUARD = GUARD_XML_TAGS


def _esc(text) -> str:
    """HTML-escape text for the newsletter, and break up "{{" / "}}" so article text can never form the
    {{UNSUBSCRIBE_URL}} / {{PREFERENCES_URL}} placeholders that agent4 substitutes per subscriber."""
    return _html.escape(str(text)).replace("{{", "&#123;&#123;").replace("}}", "&#125;&#125;")


def _safe_url(url: str | None) -> str:
    """Return url, HTML-escaped for use inside href="...", only if it uses http/https; else '#'.

    The scheme check stops javascript: URLs; the escaping stops a quote in the URL from ending the attribute.
    """
    if url and isinstance(url, str) and url.startswith(("https://", "http://")):
        return _esc(url)
    return "#"


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


def select_articles_for_category(
    category: str,
    articles: list,
    prompt_template: str,
    client: anthropic.Anthropic,
) -> list[dict]:
    if not articles:
        return []

    # Drop articles with no summary and no description — they would render as blank cards
    before = len(articles)
    articles = [a for a in articles if (a.get("summary") or "").strip() or (a.get("description") or "").strip()]
    dropped = before - len(articles)
    if dropped:
        print(f"  [select] '{category}': dropped {dropped} article(s) with no content")

    if not articles:
        return []

    # Neutral order: list position must not carry a quality signal (sorting by HN put
    # every HN article above every NewsAPI one). Sorting by URL first removes the
    # thread-timing order inherited from agent1b, so the shuffle is reproducible.
    ordered = sorted(articles, key=lambda a: a.get("url") or "")
    random.Random(category).shuffle(ordered)

    formatted = format_articles_for_selection(ordered, category)
    prompt    = prompt_template.format(category=category, articles=formatted)

    response = claude_call_with_retry(
        client,
        model=SCORING_MODEL,
        max_tokens=SELECTION_MAX_TOKENS,
        system=_PROMPT_INJECTION_GUARD,
        messages=[{"role": "user", "content": prompt}],
    )

    if not response.content:
        raise RuntimeError(f"Empty Claude response for article selection in '{category}'")
    indices = parse_indices(response.content[0].text, len(ordered))

    if not indices:
        print(f"  [warn]   no valid indices for '{category}' — falling back to first 3")
        indices = list(range(min(3, len(ordered))))

    return [ordered[i] for i in indices]


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
        f"<paper>- {neutralize_tags(p['title'])} (score: {(p.get('scores') or {}).get('total', 0)}/28)</paper>"
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
_HN    = "#22c55e"   # Hacker News score green
_SEPR  = "#ededed"   # separator on white


def _strip_markdown_headers(text: str) -> str:
    """Remove markdown header lines that Claude may generate despite prompt instructions."""
    return re.sub(r"^#{1,6}\s+[^\n]*\n?", "", text, flags=re.MULTILINE).strip()


def render_paper_card(paper: dict) -> str:
    scores       = paper.get("scores") or {}
    score        = scores.get("total", 0)
    authors_list = paper.get("authors", [])
    authors      = _esc(", ".join(authors_list[:3]) + (" et al." if len(authors_list) > 3 else ""))
    summary      = _strip_markdown_headers(paper.get("summary") or "")
    paragraphs   = [p.strip() for p in summary.split("\n\n") if p.strip()]
    pdf_url      = _safe_url(paper.get("pdf_url"))
    title        = _esc(paper.get("title", ""))

    summary_rows = "".join(
        f'<tr><td style="padding:{"0" if i == 0 else "10px"} 0 0 0;'
        f'font-family:{_F};font-size:14px;line-height:1.82;color:#8a8580;">{_esc(para)}</td></tr>\n'
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
        f'<a href="{pdf_url}" target="_blank" rel="noopener noreferrer" style="color:{_ASH};text-decoration:underline;'
        f'text-decoration-color:{_AMBER};text-underline-offset:2px;">{title}</a>'
        f'</p></td>\n'
        f'<td width="60" valign="top" style="white-space:nowrap;text-align:right;">'
        f'<p style="margin:0;font-family:{_F};line-height:1;">'
        f'<span style="font-size:26px;font-weight:700;color:{_GOLD};">{score}</span>'
        f'<br><span style="font-size:10px;color:{_AMBER};letter-spacing:1px;">/28</span>'
        f'</p></td>\n'
        f'</tr>\n'
        f'</table>\n'
        f'<p style="margin:0 0 10px 0;font-family:{_F};font-size:11px;color:{_CHAR};">{authors}</p>\n'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-bottom:12px;">'
        f'<tr><td height="2" style="background:{_AMBER};font-size:0;line-height:0;mso-line-height-rule:exactly;">&nbsp;</td></tr>'
        f'</table>\n'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">\n'
        f'{summary_rows}'
        f'</table>\n'
        f'<p style="margin:16px 0 0 0;font-family:{_F};font-size:12px;">'
        f'<a href="{pdf_url}" target="_blank" rel="noopener noreferrer" style="color:{_AMBER};text-decoration:none;">'
        f'Read paper &nbsp;&#8594;</a>'
        f'</p>\n'
        f'</td></tr>\n'
        f'</table>\n'
    )


def render_article_card(article: dict, is_last: bool = False) -> str:
    title   = _esc(article.get("title", "Untitled"))
    url     = _safe_url(article.get("url"))
    summary = _esc(article.get("summary") or article.get("description") or "")
    hn      = article.get("hn_score")

    sep = "" if is_last else f"padding-bottom:24px;border-bottom:1px solid {_SEPR};"
    mb  = "0"  if is_last else "24px"

    # Right column: concurrent voice — HN score (large) or just read link
    if hn:
        right_col = (
            f'<td class="mob-block mob-hn" width="72" valign="top" style="border-left:1px solid {_SEPR};padding-left:14px;">'
            f'<p style="margin:0 0 4px 0;font-family:{_F};font-size:10px;color:#aaaaaa;'
            f'letter-spacing:2px;text-transform:uppercase;">HN Score</p>'
            f'<p style="margin:0 0 10px 0;font-family:{_F};font-size:20px;font-weight:700;'
            f'line-height:1;color:{_HN};">&#9650;&nbsp;{hn}</p>'
            f'<p style="margin:0;font-family:{_F};font-size:12px;">'
            f'<a href="{url}" target="_blank" rel="noopener noreferrer" style="color:{_AMBER};text-decoration:none;">Read&nbsp;&#8594;</a>'
            f'</p>'
            f'</td>\n'
        )
    else:
        right_col = (
            f'<td class="mob-block mob-hn" width="72" valign="top" style="border-left:1px solid {_SEPR};padding-left:14px;">'
            f'<p style="margin:0;font-family:{_F};font-size:12px;">'
            f'<a href="{url}" target="_blank" rel="noopener noreferrer" style="color:{_AMBER};text-decoration:none;">Read&nbsp;&#8594;</a>'
            f'</p>'
            f'</td>\n'
        )

    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"'
        f' style="margin-bottom:{mb};{sep}">\n'
        f'<tr>\n'
        f'<td class="mob-block" valign="top" style="padding-right:16px;">'
        f'<p style="margin:0 0 8px 0;font-family:{_F};font-size:16px;font-weight:600;line-height:1.35;">'
        f'<a href="{url}" target="_blank" rel="noopener noreferrer" style="color:#7a6548;text-decoration:underline;text-underline-offset:2px;">{title}</a>'
        f'</p>\n'
        f'<p style="margin:0;font-family:{_F};font-size:14px;line-height:1.8;color:{_INK2};">{summary}</p>'
        f'</td>\n'
        f'{right_col}'
        f'</tr>\n'
        f'</table>\n'
    )


def compose_html(
    intro: str,
    papers: list,
    selected_by_category: dict[str, list],
    week_of: str,
    include_canada: bool = True,
) -> str:
    active_categories = [
        cat for cat in NEWS_CATEGORIES
        if not (cat == "Canada & Montreal" and not include_canada)
    ]

    category_icons = {
        "Model & Product Releases": "🚀",
        "Industry & Business":      "💼",
        "Policy, Law & Regulation": "⚖️",
        "Open Source & Tools":      "🛠️",
        "Safety & Alignment":       "🛡️",
        "Society & Culture":        "🌍",
        "Canada & Montreal":        "🍁",
    }

    # Always use the canonical custom domain for email logos — absolute URL required for email clients
    _logo_base = "https://newsletter.lofeodo.com"
    _LOGO_IMG = (
        f'<img src="{_logo_base}/images/logo-email.png" alt="{NEWSLETTER_NAME}" width="48" height="48"'
        f' style="display:block;border:0;margin-bottom:14px;">'
    )

    # CASL: physical mailing address sourced from env var (satisfies CASL when set)
    _mailing_address = os.environ.get("MAILING_ADDRESS", "").strip()
    address_html = (
        f'<p style="margin:0 0 10px 0;font-family:{_F};font-size:12px;color:{_AMBER};">'
        f'{_esc(_mailing_address)}</p>'
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
            f'<tr><td class="mob-pad" style="background:{_D2};padding:13px 40px;">'
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">\n'
            f'<tr>\n'
            # rhythm rule + large section number (Weingart marker)
            f'<td style="vertical-align:top;white-space:nowrap;padding-right:16px;">'
            f'<table role="presentation" cellspacing="0" cellpadding="0" border="0">'
            f'<tr><td width="40" height="2" style="background:{_AMBER};font-size:0;line-height:0;mso-line-height-rule:exactly;">&nbsp;</td></tr>'
            f'<tr><td style="padding-top:5px;">'
            f'<span style="font-family:{_F};font-size:28px;font-weight:700;color:{_AMBER};'
            f'line-height:1;letter-spacing:-1px;">{num}</span>'
            f'</td></tr></table>'
            f'</td>\n'
            # vertical divider + label
            f'<td style="vertical-align:middle;border-left:1px solid #333333;padding-left:16px;">'
            f'<p style="margin:0;font-family:{_F};font-size:12px;font-weight:700;'
            f'letter-spacing:2.5px;text-transform:uppercase;color:#727272;">'
            f'{icon}&nbsp; {label}</p>'
            f'</td>\n'
            f'</tr>\n'
            f'</table>\n'
            f'</td></tr>\n'
        )

    # — table of contents (2 rows × 4 cols) —
    def _toc_cell(num: str, short: str) -> str:
        return (
            f'<td class="mob-toc-cell" style="width:25%;padding:0 0 6px 0;">'
            f'<span style="font-family:{_F};font-size:11px;color:{_AMBER};font-weight:700;">{num}</span>'
            f'<span style="font-family:{_F};font-size:11px;color:#585858;">&thinsp;{short}</span>'
            f'</td>'
        )

    _toc_entries = [(f"{i+1:02d}", cat.split(" &")[0].split(",")[0].upper())
                    for i, cat in enumerate(active_categories)]
    _toc_entries.append(("RES", "RESEARCH"))
    while len(_toc_entries) % 4 != 0:
        _toc_entries.append(("", ""))
    toc_row1 = "".join(_toc_cell(n, s) for n, s in _toc_entries[:4])
    toc_row2 = "".join(_toc_cell(n, s) for n, s in _toc_entries[4:])

    # — news section rows —
    news_rows = ""
    for i, category in enumerate(active_categories):
        num      = f"{i+1:02d}"
        icon     = category_icons.get(category, "📌")
        articles = selected_by_category.get(category, [])

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

        # Section markers enable per-subscriber filtering/reordering in agent4
        news_rows += (
            f'<!-- SECTION:{category} -->\n'
            + _section_strip(num, icon, category)
            + f'<tr><td class="mob-pad" style="background:{_WHITE};padding:28px 40px;">\n'
              f'{body}\n'
              f'</td></tr>\n'
            + f'<!-- /SECTION:{category} -->\n'
        )

    # — research section —
    paper_cards   = "".join(render_paper_card(p) for p in papers)
    research_rows = (
        f'<!-- SECTION:Research Spotlights -->\n'
        + _section_strip("RES", "🔬", "Research Spotlights")
        + f'<tr><td class="mob-pad" style="background:{_D1};padding:20px 40px 32px;">\n'
          f'{paper_cards}'
          f'</td></tr>\n'
        + f'<!-- /SECTION:Research Spotlights -->\n'
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
    @media only screen and (max-width: 480px) {{
      .mob-pad {{ padding-left: 16px !important; padding-right: 16px !important; }}
      .mob-h1 {{ font-size: 26px !important; }}
      .mob-block {{ display: block !important; width: 100% !important; }}
      .mob-hn {{ border-left: none !important; padding-left: 0 !important; margin-top: 10px; }}
      .mob-toc-cell {{ width: 50% !important; display: inline-block !important; box-sizing: border-box; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">

<!-- preheader: inbox preview text = editor's note; hidden in the body -->
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#ffffff;opacity:0;">{_esc(intro[:200])}{'&zwnj;&nbsp;' * 60}</div>

<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
<tr><td class="dm-outer" style="padding:32px 12px 48px;">

  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
         style="max-width:640px;margin:0 auto;">

    <!-- ════════════════════════════════════════════
         HEADER
         ════════════════════════════════════════════ -->
    <tr><td style="background:{_D0};">
      <!-- amber top accent bar -->
      {_rule(_AMBER)}
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr><td class="mob-pad" style="padding:32px 40px 24px;">
        {_LOGO_IMG}
        <p style="margin:0 0 6px 0;font-family:{_F};font-size:12px;color:#484848;letter-spacing:5px;">&gt;_ TRANSMISSION</p>
        <h1 class="mob-h1" style="margin:0 0 0 0;font-family:{_F};font-size:48px;font-weight:700;
                   letter-spacing:-1px;line-height:1;color:{_AMBER};">{NEWSLETTER_NAME}</h1>
        <table role="presentation" width="56" cellspacing="0" cellpadding="0" border="0" style="margin:12px 0 0;"><tr><td height="3" style="background:{_AMBER};font-size:0;line-height:0;mso-line-height-rule:exactly;">&nbsp;</td></tr></table>
        {_rule("#222222", "14px")}
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
               style="margin-top:12px;">
        <tr>
          <td style="vertical-align:bottom;">
            <p style="margin:0;font-family:{_F};font-size:12px;color:#666666;
                      letter-spacing:2.5px;text-transform:uppercase;">
              Weekly AI Intelligence Dispatch
            </p>
          </td>
          <td style="vertical-align:bottom;text-align:right;white-space:nowrap;">
            <p style="margin:0;font-family:{_F};font-size:13px;color:#606060;">
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
      <tr><td class="mob-pad" style="padding:12px 40px 14px;background:#0d0d0d;">
        <!-- TOC -->
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
          <tr>{toc_row1}</tr>
          <tr>{toc_row2}</tr>
        </table>
        <!-- /TOC -->
      </td></tr>
      </table>
    </td></tr>

    <!-- ════════════════════════════════════════════
         INTRO
         ════════════════════════════════════════════ -->
    <tr><td class="mob-pad" style="background:{_CREAM};padding:28px 40px 26px;border-bottom:2px solid {_D2};">
      <p style="margin:0 0 10px 0;font-family:{_F};font-size:10px;color:#8a8070;letter-spacing:4px;">&gt;_ EDITOR&apos;S NOTE &nbsp;&middot;&middot;&middot;&nbsp; {week_of}</p>
      <p style="margin:0 0 0 0;font-family:{_F};font-size:15px;line-height:1.88;color:{_INK};">{_esc(intro)}</p>
    </td></tr>

    {news_rows}
    {research_rows}

    <!-- ════════════════════════════════════════════
         FOOTER
         ════════════════════════════════════════════ -->
    <tr><td style="background:{_D0};padding:0;">
      {_rule("#1a1a1a")}
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr><td class="mob-pad" style="padding:24px 40px;text-align:center;">
        <p style="margin:0 0 4px 0;font-family:{_F};font-size:11px;color:{_AMBER};
                  letter-spacing:3px;text-transform:uppercase;">{NEWSLETTER_NAME}</p>
        <p style="margin:0 0 14px 0;font-family:{_F};font-size:12px;color:{_AMBER};">
          You're receiving this because you subscribed.
        </p>
        {address_html}
        <p style="margin:0;font-family:{_F};font-size:12px;">
          <a href="{{{{UNSUBSCRIBE_URL}}}}" style="color:#c8b89a;text-decoration:underline;">Unsubscribe</a>
          <span style="color:{_AMBER};">&nbsp;&nbsp;·&nbsp;&nbsp;</span>
          <a href="{{{{PREFERENCES_URL}}}}" style="color:#c8b89a;text-decoration:underline;">Manage preferences</a>
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
# Main
# ---------------------------------------------------------------------------

def build_run_doc_update(newsletter_variants: dict, subject: str, selected_all: dict,
                         selected_en: dict, by_category: dict) -> dict:
    """Fields agent3 writes to the run doc when it finishes.

    The doc must stay under Firestore's 1 MiB cap, and the full article pools
    (~850 KB at ~500 articles) plus the variants (~420 KB) don't fit (2026-09-28).
    Nothing reads the pools after this point, so shrink them in the same write:
    news_summaries keeps only what shipped, news_filtered becomes counts.
    Both must stay truthy: agent_healthcheck checks them for presence.
    """
    shipped_by_category = {}
    for category in NEWS_CATEGORIES:
        seen, shipped = set(), []
        for a in selected_all.get(category, []) + selected_en.get(category, []):
            if a.get("url") not in seen:
                seen.add(a.get("url"))
                shipped.append(a)
        if shipped:
            shipped_by_category[category] = shipped
    return {
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
        with open("prompts/article_selection_prompt.txt", "r", encoding="utf-8") as f:
            selection_prompt = f.read()
        with open("prompts/intro_prompt.txt", "r", encoding="utf-8") as f:
            intro_prompt = f.read()

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
            print(f"[agent3]  Loaded paper_summaries and news_summaries from Firestore")
        else:
            with open(os.path.join(DATA_DIR, "paper_summaries.json"), "r", encoding="utf-8") as f:
                paper_data = json.load(f)
            with open(os.path.join(DATA_DIR, "news_summaries.json"), "r", encoding="utf-8") as f:
                news_data = json.load(f)
            papers      = paper_data["papers"]
            by_category = news_data["by_category"]

        client = tracing.make_client("agent3", run_id)

        print("\n=== Selecting articles per category ===")
        # Two selection passes per category:
        #   selected_all — full pool (French + English), used when include_french=True
        #   selected_en  — English-only pool, used when include_french=False
        # The second pass is skipped when the category has no French articles.
        selected_all: dict[str, list] = {}
        selected_en:  dict[str, list] = {}

        for category in NEWS_CATEGORIES:
            articles = by_category.get(category, [])
            print(f"  [{category}] {len(articles)} articles available", end="")

            if not articles:
                print(" → no articles, using fallback")
                selected_all[category] = []
                selected_en[category]  = []
                continue

            full_selected = select_articles_for_category(category, articles, selection_prompt, client)
            selected_all[category] = full_selected

            en_articles = [a for a in articles if a.get("language") != "fr"]
            if len(en_articles) < len(articles):
                print(f" → selected {len(full_selected)} (re-running English-only)", end="")
                selected_en[category] = select_articles_for_category(category, en_articles, selection_prompt, client)
            else:
                selected_en[category] = full_selected

            print(f" → {len(full_selected)} (all) / {len(selected_en[category])} (en)")

        print("\n=== Writing intro paragraph ===")
        intro = write_intro(papers, selected_all, intro_prompt, client)
        print(f"  Intro: {intro[:100]}...")

        print("\n=== Composing HTML variants ===")
        week_of = newsletter_send_date().strftime("%B %d, %Y")
        newsletter_variants: dict[str, str] = {}
        for key, prefs in NEWSLETTER_VARIANTS.items():
            selection = selected_all if prefs["include_french"] else selected_en
            html = compose_html(intro, papers, selection, week_of, include_canada=prefs["include_canada"])
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

        # Copy to public dir so Firebase Hosting serves it as the live preview
        public_preview = os.path.join(
            os.path.dirname(__file__), "..", "public", "newsletter", "latest.html"
        )
        with open(public_preview, "w", encoding="utf-8") as f:
            f.write(newsletter_variants["0_0"])
        print(f"  Saved {public_preview} (public preview)")

        if USE_FIRESTORE:
            from google.cloud import firestore as _fs
            # The run doc must stay under Firestore's 1 MiB cap; see build_run_doc_update().
            _fs.Client(project=GCP_PROJECT_ID).collection(FIRESTORE_COLLECTION).document(run_id).update(
                build_run_doc_update(newsletter_variants, f"{NEWSLETTER_NAME} — {week_of}",
                                     selected_all, selected_en, by_category)
            )
            print(f"  Written newsletter_variants + newsletter_html (0_0) to Firestore; pruned article pools")

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n--- Done in {elapsed:.1f}s ---")

        total_selected = sum(len(v) for v in selected_all.values())
        print(f"Papers:   {len(papers)}")
        print(f"Articles: {total_selected} selected across {len(NEWS_CATEGORIES)} categories")
        print(f"Variants: {list(newsletter_variants.keys())}")
    except Exception as e:
        _record_failure(run_id, "agent3", e)
        raise
    finally:
        tracing.flush()


if __name__ == "__main__":
    run(run_id="local-debug")