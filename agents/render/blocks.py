# agents/render/blocks.py
#
# The email's building blocks: paper card, article card, section strip, TOC cell, share strip, thin rule.
# Table-based, all-inline styles (Gmail/Outlook safe).

from .palette import AI_LINK, AMBER, ASH, CHAR, CREAM, D0, D2, F, GOLD, HN, INK, INK2, SEPR, WHITE
from .text import esc, safe_url, strip_markdown_headers


def rule(color: str, my: str = "0") -> str:
    """A thin horizontal rule (1px, given colour)."""
    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"'
        f' style="margin:{my} 0;">'
        f'<tr><td height="1" style="background:{color};font-size:0;line-height:0;">&nbsp;</td></tr>'
        f'</table>\n'
    )


def render_paper_card(paper: dict) -> str:
    """The spotlight paper: a dark, amber-accented hero block, visually distinct from the white news cards."""
    scores       = paper.get("scores") or {}
    upvotes      = scores.get("upvotes")
    authors_list = paper.get("authors", [])
    authors      = esc(", ".join(authors_list[:3]) + (" et al." if len(authors_list) > 3 else ""))
    summary      = " ".join(strip_markdown_headers(paper.get("summary") or "").split())
    # Link to the Hugging Face page; older runs and the fallback path may only have the PDF.
    link         = safe_url(paper.get("hf_url") or paper.get("pdf_url"))
    on_hf        = bool(paper.get("hf_url"))
    title        = esc(paper.get("title", ""))

    stat = ""
    if isinstance(upvotes, int) and upvotes > 0:
        stat = (
            f'<td align="right" valign="top" style="white-space:nowrap;font-family:{F};font-size:12px;color:{GOLD};">'
            f'<span style="font-weight:700;">&#9650;&nbsp;{upvotes:,}</span>'
            f'<span style="color:{CHAR};">&nbsp;upvotes</span></td>'
        )

    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">\n'
        f'<tr><td class="mob-pad" style="background:#17140f;padding:22px 32px 24px 28px;border-left:4px solid {AMBER};border-bottom:3px solid {AMBER};">\n'
        # kicker row: label left, HF upvotes right
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:0 0 9px 0;"><tr>'
        f'<td valign="top" style="font-family:{F};font-size:11px;font-weight:700;'
        f'letter-spacing:3px;text-transform:uppercase;color:{AMBER};">&#9733;&nbsp; Paper of the week</td>'
        f'{stat}</tr></table>\n'
        # title
        f'<h2 class="mob-spot-title" style="margin:0 0 4px 0;font-family:{F};font-size:22px;font-weight:700;'
        f'line-height:1.25;letter-spacing:-0.3px;color:{CREAM};">'
        f'<a href="{link}" target="_blank" rel="noopener noreferrer" style="color:{CREAM};text-decoration:none;">{title}</a>'
        f'</h2>\n'
        f'<p style="margin:0 0 11px 0;font-family:{F};font-size:12px;color:#8a8580;">{authors}</p>\n'
        # hook
        f'<p style="margin:0 0 15px 0;font-family:{F};font-size:15px;line-height:1.55;color:{ASH};">{esc(summary)}</p>\n'
        # bulletproof button
        f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" class="mob-block">'
        f'<tr><td align="center" bgcolor="{AMBER}" style="background:{AMBER};">'
        f'<a href="{link}" target="_blank" rel="noopener noreferrer" class="mob-btn" '
        f'style="display:inline-block;padding:9px 20px;font-family:{F};font-size:13px;font-weight:700;'
        f'letter-spacing:1px;color:{D0};text-decoration:none;">'
        f'{"Read on Hugging Face" if on_hf else "Read the paper"} &nbsp;&#8594;</a>'
        f'</td></tr></table>\n'
        f'</td></tr>\n'
        f'</table>\n'
    )


def render_article_card(article: dict, is_last: bool = False, accent: str = AMBER, link_color: str = AI_LINK) -> str:
    title   = esc(article.get("title", "Untitled"))
    url     = safe_url(article.get("url"))
    summary = esc(article.get("summary") or article.get("description") or "")
    hn      = article.get("hn_score")

    sep = "" if is_last else f"padding-bottom:24px;border-bottom:1px solid {SEPR};"
    mb  = "0"  if is_last else "24px"

    # Right column: concurrent voice — HN score (large) or just read link
    if hn:
        right_col = (
            f'<td class="mob-block mob-hn" width="72" valign="top" style="border-left:1px solid {SEPR};padding-left:14px;">'
            f'<p style="margin:0 0 4px 0;font-family:{F};font-size:10px;color:#aaaaaa;'
            f'letter-spacing:2px;text-transform:uppercase;">HN Score</p>'
            f'<p style="margin:0 0 10px 0;font-family:{F};font-size:20px;font-weight:700;'
            f'line-height:1;color:{HN};">&#9650;&nbsp;{hn}</p>'
            f'<p style="margin:0;font-family:{F};font-size:12px;">'
            f'<a href="{url}" target="_blank" rel="noopener noreferrer" style="color:{accent};text-decoration:none;">Read&nbsp;&#8594;</a>'
            f'</p>'
            f'</td>\n'
        )
    else:
        right_col = (
            f'<td class="mob-block mob-hn" width="72" valign="top" style="border-left:1px solid {SEPR};padding-left:14px;">'
            f'<p style="margin:0;font-family:{F};font-size:12px;">'
            f'<a href="{url}" target="_blank" rel="noopener noreferrer" style="color:{accent};text-decoration:none;">Read&nbsp;&#8594;</a>'
            f'</p>'
            f'</td>\n'
        )

    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"'
        f' style="margin-bottom:{mb};{sep}">\n'
        f'<tr>\n'
        f'<td class="mob-block" valign="top" style="padding-right:16px;">'
        f'<p style="margin:0 0 8px 0;font-family:{F};font-size:16px;font-weight:600;line-height:1.35;">'
        f'<a href="{url}" target="_blank" rel="noopener noreferrer" style="color:{link_color};text-decoration:underline;text-underline-offset:2px;">{title}</a>'
        f'</p>\n'
        f'<p style="margin:0;font-family:{F};font-size:14px;line-height:1.8;color:{INK2};">{summary}</p>'
        f'</td>\n'
        f'{right_col}'
        f'</tr>\n'
        f'</table>\n'
    )


def section_strip(num: str, icon: str, label: str, accent: str,
                  topic_label: str | None = None, topic_accent: str | None = None) -> str:
    """Dark section-number strip (editorial zine style). In an email that mixes topics, `topic_label` names the
    section's topic in that topic's accent colour ("Finance · Crypto & Fintech")."""
    if topic_label:
        label = f'<span style="color:{topic_accent or accent};">{topic_label}</span> &middot; {label}'
    return (
        f'<tr><td class="mob-pad" style="background:{D2};padding:13px 40px;">'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">\n'
        f'<tr>\n'
        # rhythm rule + large section number (Weingart marker)
        f'<td style="vertical-align:top;white-space:nowrap;padding-right:16px;">'
        f'<table role="presentation" cellspacing="0" cellpadding="0" border="0">'
        f'<tr><td width="40" height="2" style="background:{accent};font-size:0;line-height:0;mso-line-height-rule:exactly;">&nbsp;</td></tr>'
        f'<tr><td style="padding-top:5px;">'
        f'<span style="font-family:{F};font-size:28px;font-weight:700;color:{accent};'
        f'line-height:1;letter-spacing:-1px;">{num}</span>'
        f'</td></tr></table>'
        f'</td>\n'
        # vertical divider + label
        f'<td style="vertical-align:middle;border-left:1px solid #333333;padding-left:16px;">'
        f'<p style="margin:0;font-family:{F};font-size:12px;font-weight:700;'
        f'letter-spacing:2.5px;text-transform:uppercase;color:#727272;">'
        f'{icon}&nbsp; {label}</p>'
        f'</td>\n'
        f'</tr>\n'
        f'</table>\n'
        f'</td></tr>\n'
    )


def toc_cell(num: str, short: str, accent: str) -> str:
    """One cell of the table of contents (2 rows × 4 cols)."""
    return (
        f'<td class="mob-toc-cell" style="width:25%;padding:0 0 6px 0;">'
        f'<span style="font-family:{F};font-size:11px;color:{accent};font-weight:700;">{num}</span>'
        f'<span style="font-family:{F};font-size:11px;color:#585858;">&thinsp;{short}</span>'
        f'</td>'
    )


def share_strip() -> str:
    """One-line share strip, after the 1st news section (outside SECTION markers; agent4 re-places it when it
    reorders sections, keyed on the SHARE markers)."""
    return (
        f'<!-- SHARE -->\n'
        f'<tr><td class="mob-share" style="background:{WHITE};padding:0 40px 22px;">'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td style="background:#f3e4c0;border:2px solid #b8975a;border-radius:999px;padding:7px 8px 7px 18px;" class="mob-share-in">'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td style="font-family:{F};font-size:11px;color:{INK};white-space:nowrap;vertical-align:middle;">'
        f'<span class="mob-hide">&#128140;&nbsp; </span>Enjoying the newsletter? <strong>Consider sharing!</strong></td>'
        f'<td align="right" style="vertical-align:middle;white-space:nowrap;padding-left:8px;">'
        f'<a href="https://newsletter.lofeodo.com/share.html" style="display:inline-block;font-family:{F};'
        f'font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:#f3e4c0;'
        f'background:{INK};border-radius:999px;padding:6px 14px;text-decoration:none;" class="mob-share-btn">Share &#8599;</a>'
        f'</td></tr></table>'
        f'</td></tr></table>'
        f'</td></tr>\n'
        f'<!-- /SHARE -->\n'
    )


def section_rows(name: str, num: str, icon: str, body: str, accent: str, marker: str | None = None,
                 topic_label: str | None = None, topic_accent: str | None = None) -> str:
    """A news section: SECTION markers around the strip and the white body row. The markers enable
    per-subscriber filtering/reordering in agent4; `marker` overrides the name inside them."""
    marker = marker or name
    return (
        f'<!-- SECTION:{marker} -->\n'
        + section_strip(num, icon, name, accent, topic_label, topic_accent)
        + f'<tr><td class="mob-pad" style="background:{WHITE};padding:28px 40px;">\n'
          f'{body}\n'
          f'</td></tr>\n'
        + f'<!-- /SECTION:{marker} -->\n'
    )


def empty_note(text: str) -> str:
    return (
        f'<p style="font-family:{F};font-size:13px;color:#aaaaaa;'
        f'font-style:italic;margin:0;">{text}</p>'
    )


def research_rows(papers: list) -> str:
    """The spotlight paper section: leads the issue, right under the editor's note."""
    paper_cards = "".join(render_paper_card(p) for p in papers)
    return (
        f'<!-- SECTION:Research Spotlights -->\n'
        + f'<tr><td style="padding:0;background:{D0};">\n'
          f'{paper_cards}'
          f'</td></tr>\n'
        + f'<!-- /SECTION:Research Spotlights -->\n'
    )
