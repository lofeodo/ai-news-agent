# agents/widgets/ui.py
#
# Email primitives every widget draws with, so they all look like one newsletter: a dark card in the style of
# the paper of the week (accent bar on the left), a stat row, a small data table, a text sparkline, deltas
# that read without colour (arrow + sign + colour) and the source / "as of" attribution line.
# Table layout and inline styles only (C7). Everything from a third party or a model goes through esc() or
# safe_url(); pass Raw(...) only for HTML built here.

import re
from datetime import date, datetime

from render.palette import AMBER, ASH, CHAR, CREAM, F
from render.text import esc, safe_url

CARD_BG = "#17140f"
RULE = "#2a2620"
UP = "#4ade80"      # readable on the dark card, in light and dark mode
DOWN = "#f87171"
FLAT = "#a8a29e"
SPARK = "▁▂▃▄▅▆▇█"
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class Raw(str):
    """HTML built by these primitives: passed through unescaped."""


def cell(value) -> str:
    if value is None:
        return "&ndash;"
    return value if isinstance(value, Raw) else esc(value)


def link(text, url, color: str = CREAM, underline: bool = False) -> Raw:
    deco = "underline" if underline else "none"
    return Raw(f'<a href="{safe_url(url)}" target="_blank" rel="noopener noreferrer" '
               f'style="color:{color};text-decoration:{deco};">{cell(text)}</a>')


def badge(text: str, color: str) -> Raw:
    return Raw(f'<span style="display:inline-block;font-family:{F};font-size:9px;font-weight:700;letter-spacing:1.5px;'
               f'color:{CARD_BG};background:{color};padding:1px 5px;margin-left:6px;vertical-align:1px;">'
               f'{esc(text)}</span>')


def muted(text) -> Raw:
    return Raw(f'<span style="color:{CHAR};">{cell(text)}</span>')


def sparkline(values) -> str:
    """Eight-level text sparkline (▁ lowest, █ highest) of the numbers given; missing values are skipped and a
    flat series is drawn mid-height. Returns plain text."""
    nums = [v for v in values or [] if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not nums:
        return ""
    lo, hi = min(nums), max(nums)
    if hi == lo:
        return SPARK[3] * len(nums)
    return "".join(SPARK[round((v - lo) / (hi - lo) * (len(SPARK) - 1))] for v in nums)


def spark_html(values, color: str = ASH, label: str = "trend") -> Raw:
    text = sparkline(values)
    if not text:
        return Raw("")
    return Raw(f'<span role="img" aria-label="{esc(label)}" style="font-family:{F};letter-spacing:1px;'
               f'color:{color};">{text}</span>')


def delta(change, decimals: int = 2, unit: str = "", up_good: bool | None = None) -> Raw:
    """A change as arrow + signed number + colour, e.g. "▲ +0.12%". `up_good` None means neither direction is
    good (an exchange rate): up and down still get their arrow and sign, in a neutral colour."""
    if not isinstance(change, (int, float)) or isinstance(change, bool):
        return Raw(f'<span style="color:{FLAT};">&ndash;</span>')
    rounded = round(change, decimals)
    if rounded == 0:
        arrow, color, text = "&#9654;", FLAT, f"{0:.{decimals}f}"
    else:
        up = rounded > 0
        arrow = "&#9650;" if up else "&#9660;"
        color = FLAT if up_good is None else (UP if up == up_good else DOWN)
        text = f"{'+' if up else '−'}{abs(rounded):.{decimals}f}"
    return Raw(f'<span style="color:{color};white-space:nowrap;">{arrow}&nbsp;{text}{esc(unit)}</span>')


def fmt_date(value, with_year: bool = False) -> str:
    """'2026-10-15' or an ISO datetime -> 'Oct 15' (or 'Oct 15, 2026'); anything unparsable -> ''."""
    if isinstance(value, (date, datetime)):
        d = value
    else:
        try:
            d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return ""
    out = f"{d.strftime('%b')} {d.day}"
    return f"{out}, {d.year}" if with_year else out


def attribution(sources, as_of=None, note: str | None = None) -> Raw:
    """'Source: A, B · as of Oct 15 · note'. `sources` is [(name, url or None), ...]."""
    names = [link(n, u, CHAR, underline=True) if u else esc(n) for n, u in sources or []]
    parts = []
    if names:
        parts.append("Source: " + ", ".join(names))
    when = fmt_date(as_of, with_year=True) if as_of else ""
    if when:
        parts.append(f"as of {when}")
    if note:
        parts.append(esc(note))
    return Raw(f'<p style="margin:14px 0 0 0;font-family:{F};font-size:11px;line-height:1.5;color:{CHAR};">'
               + " &middot; ".join(parts) + "</p>")


def stat_row(stats) -> Raw:
    """Up to four big numbers side by side, stacked on phones. `stats` is [(label, value, sub), ...]."""
    cells = []
    for label, value, sub in list(stats)[:4]:
        cells.append(
            f'<td class="mob-block" valign="top" style="padding:0 16px 12px 0;">'
            f'<p style="margin:0 0 3px 0;font-family:{F};font-size:10px;letter-spacing:2px;text-transform:uppercase;'
            f'color:{CHAR};">{cell(label)}</p>'
            f'<p style="margin:0;font-family:{F};font-size:20px;font-weight:700;line-height:1.2;color:{CREAM};">'
            f'{cell(value)}</p>'
            + (f'<p style="margin:3px 0 0 0;font-family:{F};font-size:12px;color:{ASH};">{cell(sub)}</p>' if sub else "")
            + '</td>')
    return Raw('<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
               + "".join(cells) + "</tr></table>")


def data_table(headers, rows, numeric=(), caption: str | None = None) -> Raw:
    """A real data table: header cells with scope="col", thin rules between rows. Columns whose index is in
    `numeric` are right-aligned and never wrap; the first column wraps, so the table fits a phone. The font is
    set once on the table (the email is standards mode, so cells inherit it) to keep ten rows well under 8 KB."""
    head = "".join(
        f'<th scope="col" align="{"right" if i in numeric else "left"}" style="padding:0 0 6px {"8px" if i else "0"};'
        f'font-size:10px;font-weight:400;letter-spacing:1.5px;text-transform:uppercase;'
        f'color:{CHAR};border-bottom:1px solid {RULE};">{cell(h)}</th>'
        for i, h in enumerate(headers))
    body = []
    for r, row in enumerate(rows):
        border = f"border-top:1px solid {RULE};" if r else ""
        tds = "".join(
            f'<td{" align=right" if i in numeric else ""} valign=top style="padding:7px 0 7px {"8px" if i else "0"};'
            f'{border}{"white-space:nowrap;" if i in numeric else ""}">{cell(v)}</td>'
            for i, v in enumerate(row))
        body.append(f"<tr>{tds}</tr>")
    cap = (f'<caption style="text-align:left;padding:0 0 8px 0;font-size:12px;">'
           f'{esc(caption)}</caption>') if caption else ""
    return Raw(f'<table width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;'
               f'font-family:{F};font-size:13px;line-height:1.4;color:{ASH};">'
               f'{cap}<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>')


def item_list(items) -> Raw:
    """Simple stacked list: [(title_html, meta_html), ...], one per row with a thin rule between."""
    out = []
    for i, (title, meta) in enumerate(items):
        border = f"border-top:1px solid {RULE};" if i else ""
        out.append(
            f'<tr><td style="padding:8px 0;{border}">'
            f'<p style="margin:0;font-family:{F};font-size:14px;font-weight:600;line-height:1.35;color:{CREAM};">'
            f'{cell(title)}</p>'
            + (f'<p style="margin:3px 0 0 0;font-family:{F};font-size:12px;line-height:1.5;color:{CHAR};">{cell(meta)}</p>'
               if meta else "")
            + "</td></tr>")
    return Raw('<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
               + "".join(out) + "</table>")


def card(widget_id: str, title: str, body: str, accent: str, icon: str = "", aside=None,
         footer: str = "") -> str:
    """The widget shell: one table row, dark card with the newsletter's accent bar, kicker title (and an
    optional right-hand aside), the body, then the attribution line. Marked <!-- WIDGET:id --> for tools."""
    accent = accent if isinstance(accent, str) and _COLOR_RE.match(accent) else AMBER
    kicker = f"{esc(icon)}&nbsp; {esc(title)}" if icon else esc(title)
    right = (f'<td align="right" valign="top" style="white-space:nowrap;font-family:{F};font-size:11px;'
             f'color:{CHAR};">{cell(aside)}</td>') if aside else ""
    widget_id = esc(widget_id)
    return (
        f'<!-- WIDGET:{widget_id} -->\n'
        f'<tr><td class="mob-pad" style="background:{CARD_BG};padding:22px 32px 22px 28px;'
        f'border-left:4px solid {accent};border-bottom:1px solid {RULE};">\n'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:0 0 12px 0;">'
        f'<tr><td valign="top" style="font-family:{F};font-size:11px;font-weight:700;letter-spacing:3px;'
        f'text-transform:uppercase;color:{accent};">{kicker}</td>{right}</tr></table>\n'
        f'{body}\n{footer}\n'
        f'</td></tr>\n'
        f'<!-- /WIDGET:{widget_id} -->\n'
    )
