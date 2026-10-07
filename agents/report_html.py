# agents/report_html.py
#
# Turns the health check's plain-text report into an HTML email styled like the newsletter
# (dark header, amber accents, numbered section strips, monospace). The text report stays the source
# of truth (tests and logs use it); this module only presents it. Anything it does not recognise is
# rendered as a plain monospace block, so a new report section can never break or hide the email.
# Table-based with inline styles only (Gmail/Outlook safe), same constraints as agent3's newsletter.

import html
import re

_F     = "'Menlo','Cascadia Mono','Consolas','Courier New',monospace"
_D0    = "#080808"
_D2    = "#191919"
_CREAM = "#faf9f4"
_INK   = "#111111"
_INK2  = "#555555"
_AMBER = "#c8b89a"
_GOLD  = "#d4a843"
_GREEN = "#22c55e"
_RED   = "#ef4444"
_SEPR  = "#ededed"
_MUTED = "#8a8580"
_ASH   = "#d4cfc8"
_LOGO  = "https://newsletter.lofeodo.com/images/logo-email.png"

_PIPELINE_RE = re.compile(r"^Pipeline run (\S+) \(started ([^)]*)\) (completed successfully\.|has problems:)")
_AGENT_ROW_RE = re.compile(r"^- (\w+): ([\d,]+) tokens \(~\$([\d.,]+|n/a)\)")


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _highlight(escaped: str) -> str:
    """Colour status words in already-escaped text."""
    escaped = re.sub(r"\b(DRIFT FLAGGED|DRIFT)\b", f'<span style="color:{_RED};font-weight:700;">\\1</span>', escaped)
    escaped = re.sub(r"\b(no drift|ok)\b", f'<span style="color:{_GREEN};font-weight:700;">\\1</span>', escaped)
    return escaped


def _chip(text: str, color: str) -> str:
    return (f'<span style="font-family:{_F};font-size:10px;font-weight:700;letter-spacing:2px;'
            f'text-transform:uppercase;color:{color};border:1px solid {color};padding:3px 7px;'
            f'white-space:nowrap;">{_e(text)}</span>')


def _strip(num: str, label: str, chip: str = "") -> str:
    """Dark numbered section strip, as in the newsletter."""
    chip_cell = f'<td align="right" style="vertical-align:middle;padding-left:10px;">{chip}</td>' if chip else ""
    return (
        f'<tr><td class="pad" style="background:{_D2};padding:12px 32px;">'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
        f'<td width="56" style="vertical-align:top;white-space:nowrap;padding-right:14px;">'
        f'<table role="presentation" cellspacing="0" cellpadding="0" border="0">'
        f'<tr><td width="32" height="2" style="background:{_AMBER};font-size:0;line-height:0;">&nbsp;</td></tr>'
        f'<tr><td style="padding-top:4px;"><span style="font-family:{_F};font-size:24px;font-weight:700;'
        f'color:{_AMBER};line-height:1;letter-spacing:-1px;">{_e(num)}</span></td></tr></table></td>'
        f'<td style="vertical-align:middle;border-left:1px solid #333333;padding-left:14px;">'
        f'<p style="margin:0;font-family:{_F};font-size:12px;font-weight:700;letter-spacing:2.5px;'
        f'text-transform:uppercase;color:#9a9a9a;">{_e(label)}</p></td>'
        f'{chip_cell}</tr></table></td></tr>\n'
    )


def _card(inner: str) -> str:
    return f'<tr><td class="pad" style="background:#ffffff;padding:20px 32px 22px;">{inner}</td></tr>\n'


def _lines_html(lines: list[str]) -> str:
    """Generic block body: '- x' bullets, '(note)' lines muted, everything else a paragraph."""
    out = []
    for raw in lines:
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip())
        t = raw.strip()
        pad = 18 if indent >= 6 else 0
        if t.startswith("(") and t.endswith(")"):
            out.append(f'<p style="margin:10px 0 0 0;font-family:{_F};font-size:11px;line-height:1.7;'
                       f'color:{_MUTED};font-style:italic;">{_e(t)}</p>')
        elif t.startswith("- "):
            out.append(f'<p style="margin:0 0 6px {pad}px;padding-left:16px;text-indent:-16px;font-family:{_F};'
                       f'font-size:13px;line-height:1.7;color:{_INK};"><span style="color:{_AMBER};">&#9642;</span>'
                       f'&nbsp;{_highlight(_e(t[2:]))}</p>')
        else:
            out.append(f'<p style="margin:0 0 8px {pad}px;font-family:{_F};font-size:13px;line-height:1.75;'
                       f'color:{_INK};">{_highlight(_e(t))}</p>')
    return "".join(out)


def _chip_for(text: str) -> str:
    low = text.lower()
    if "drift flagged" in low:
        return _chip("drift flagged", _RED)
    if "failed" in low.split("\n")[0]:
        return _chip("failed", _RED)
    if "skipped" in low.split("\n")[0]:
        return _chip("skipped", _MUTED)
    if "not enough history" in low or "no tracked runs" in low:
        return _chip("warming up", _MUTED)
    if "no drift" in low:
        return _chip("no drift", _GREEN)
    return ""


def _tile(value: str, label: str) -> str:
    return (f'<td align="center" style="padding:12px 6px;background:{_CREAM};border:1px solid {_SEPR};">'
            f'<p style="margin:0;font-family:{_F};font-size:20px;font-weight:700;color:{_INK};">{_e(value)}</p>'
            f'<p style="margin:4px 0 0 0;font-family:{_F};font-size:10px;letter-spacing:2px;text-transform:uppercase;'
            f'color:{_MUTED};">{_e(label)}</p></td>')


def _usage_html(lines: list[str]) -> str:
    """Stat tiles + a bar per agent for the 'LLM usage' block; any other line falls back to generic."""
    head = re.match(r"LLM usage this run: ([\d,]+) tokens \(([\d,]+) in / ([\d,]+) out, ([\d,]+) calls\)", lines[0])
    if not head:
        return _lines_html(lines)
    total = int(head.group(1).replace(",", ""))
    cost = ""
    rows, rest = [], []
    for ln in lines[1:]:
        t = ln.strip()
        m = _AGENT_ROW_RE.match(t)
        if m:
            rows.append((m.group(1), int(m.group(2).replace(",", "")), m.group(3)))
        elif t.startswith("cost:"):
            cm = re.search(r"LangSmith (\S+)", t)
            cost = cm.group(1) if cm and cm.group(1) != "n/a" else (re.search(r"estimate (\S+)", t).group(1)
                                                                   if re.search(r"estimate (\S+)", t) else "")
            rest.append(ln)
        else:
            rest.append(ln)
    tokens = f"{total / 1_000_000:.2f}M" if total >= 1_000_000 else f"{total / 1000:.0f}K"
    tiles = (f'<table role="presentation" width="100%" cellspacing="6" cellpadding="0" border="0" style="margin:0 -6px 10px;">'
             f'<tr>{_tile(tokens, "tokens")}{_tile(head.group(4), "calls")}{_tile(cost or "n/a", "cost")}</tr></table>')
    biggest = max((r[1] for r in rows), default=1) or 1
    bars = ""
    for name, toks, usd in rows:
        pct = max(2, round(toks / biggest * 100))
        bars += (
            f'<tr><td style="padding:5px 10px 5px 0;font-family:{_F};font-size:12px;color:{_INK};white-space:nowrap;">{_e(name)}</td>'
            f'<td width="100%" style="padding:5px 0;"><table role="presentation" width="{pct}%" cellspacing="0" cellpadding="0" border="0">'
            f'<tr><td height="8" style="background:{_AMBER};font-size:0;line-height:0;">&nbsp;</td></tr></table></td>'
            f'<td align="right" style="padding:5px 0 5px 10px;font-family:{_F};font-size:12px;color:{_INK2};white-space:nowrap;">'
            f'{toks:,} <span style="color:{_MUTED};">~{_e(usd if usd == "n/a" else "$" + usd)}</span></td></tr>'
        )
    bar_table = (f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
                 f'style="margin-bottom:6px;">{bars}</table>') if bars else ""
    return tiles + bar_table + _lines_html([ln for ln in rest if not ln.strip().startswith("cost:")])


_TITLE_PREFIXES = ("Drift check: ", "Usage drift: ", "Usage check: ", "Summary faithfulness: ")


def _sections(blocks: list[str]) -> list[tuple[str, str, str]]:
    """[(label, chip_html, body_html)] for every block after the pipeline line."""
    out = []
    for b in blocks:
        lines = b.split("\n")
        first = lines[0]
        if first.startswith(_TITLE_PREFIXES) and len(lines) == 1:
            # The strip already says what this is; a one-line status needn't repeat it.
            stripped = b
            for pre in _TITLE_PREFIXES:
                if stripped.startswith(pre):
                    stripped = stripped[len(pre):]
                    break
            stripped = stripped[:1].upper() + stripped[1:]
            lines = [stripped]
        if first.startswith("DRAFT CHECK:"):
            continue  # shown as a callout in the header area instead
        if first.startswith("LLM usage"):
            out.append(("LLM usage", "", _usage_html(lines)))
        elif first.startswith("Usage drift") or first.startswith("Usage check"):
            out.append(("Usage drift", _chip_for(b), _lines_html(lines)))
        elif first.startswith("Drift check"):
            out.append(("Drift", _chip_for(b), _lines_html(lines)))
        elif first.startswith("Summary faithfulness"):
            out.append(("Summary faithfulness", _chip_for(b), _lines_html(lines)))
        elif first.startswith("Reader clicks"):
            out.append(("Reader clicks", _chip_for(b), _lines_html(lines)))
        else:
            out.append(("Details", "", f'<pre style="margin:0;font-family:{_F};font-size:12px;line-height:1.6;'
                                       f'white-space:pre-wrap;color:{_INK};">{_e(b)}</pre>'))
    return out


def render(message: str, healthy: bool, mode: str, newsletter_name: str = "Latent SpaceMail") -> str:
    """HTML email body for one health check report."""
    blocks = [b for b in re.split(r"\n\s*\n", message.strip()) if b.strip()]
    first = blocks[0] if blocks else ""
    m = _PIPELINE_RE.match(first.split("\n")[0])

    meta = ""
    problems_html = ""
    sections: list[tuple[str, str, str]] = []
    note = ""
    if m:
        run_id, started, state = m.groups()
        meta = (f'<p style="margin:0;font-family:{_F};font-size:11px;line-height:1.8;color:{_MUTED};">'
                f'RUN&nbsp;<span style="color:{_ASH};">{_e(run_id)}</span><br>'
                f'STARTED&nbsp;<span style="color:{_ASH};">{_e(started)}</span></p>')
        rest_blocks = blocks[1:]
        if state == "has problems:":
            probs = [ln for ln in first.split("\n")[1:] if ln.strip()]
            # The report puts a blank line after the header, so the list is the next block(s) of "- ..." lines.
            while rest_blocks and all(ln.startswith("- ") for ln in rest_blocks[0].split("\n") if ln.strip()):
                probs += [ln for ln in rest_blocks[0].split("\n") if ln.strip()]
                rest_blocks = rest_blocks[1:]
            body = "".join(
                f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:0 0 8px;">'
                f'<tr><td style="border-left:3px solid {_RED};background:#fdf2f2;padding:10px 14px;font-family:{_F};'
                f'font-size:13px;line-height:1.7;color:{_INK};">{_e(p[2:] if p.startswith("- ") else p)}</td></tr></table>'
                for p in probs
            )
            problems_html = _strip("!!", "Problems", _chip(f"{len(probs)} flagged", _RED)) + _card(body)
        else:
            rest = first[m.end():].strip()
            problems_html = _strip("OK", "Pipeline", _chip("healthy", _GREEN)) + _card(
                f'<p style="margin:0;font-family:{_F};font-size:13px;line-height:1.75;color:{_INK};">'
                f'Run completed successfully. {_e(rest) if rest else "No problems detected."}</p>')
        sections = _sections(rest_blocks)
        if any(b.startswith("DRAFT CHECK:") for b in blocks):
            dc = next(b for b in blocks if b.startswith("DRAFT CHECK:"))
            note = (f'<tr><td class="pad" style="background:{_CREAM};padding:14px 32px;border-bottom:2px solid {_D2};">'
                    f'<p style="margin:0;font-family:{_F};font-size:12px;line-height:1.75;color:{_INK2};">'
                    f'<span style="color:{_GOLD};font-weight:700;letter-spacing:3px;">DRAFT CHECK</span>&nbsp; '
                    f'{_e(dc[len("DRAFT CHECK:"):].strip())}</p></td></tr>\n')
    else:
        # Not a normal report (stale run, no run at all, the checker itself crashed): show it verbatim.
        sections = [("Details", "", f'<pre style="margin:0;font-family:{_F};font-size:12px;line-height:1.6;'
                                    f'white-space:pre-wrap;color:{_INK};">{_e(message)}</pre>')]

    body_rows = problems_html
    for i, (label, chip, inner) in enumerate(sections, 1):
        body_rows += _strip(f"{i:02d}", label, chip) + _card(inner)

    status_text, status_color, band_bg = (("All clear", _GREEN, "#0d2616") if healthy
                                          else ("Problem detected", _RED, "#2a0f0f"))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_e(newsletter_name)} {_e(mode)} health check</title>
  <style>
    @media only screen and (max-width: 480px) {{
      .pad {{ padding-left: 16px !important; padding-right: 16px !important; }}
      .h1 {{ font-size: 24px !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:#111111;-webkit-text-size-adjust:100%;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#111111;">
<tr><td style="padding:24px 12px 40px;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:640px;margin:0 auto;">
    <tr><td height="1" style="background:{_AMBER};font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td class="pad" style="background:{_D0};padding:28px 32px 22px;">
      <img src="{_LOGO}" alt="{_e(newsletter_name)}" width="40" height="40" style="display:block;border:0;margin-bottom:12px;">
      <p style="margin:0 0 6px 0;font-family:{_F};font-size:11px;color:#6a6560;letter-spacing:5px;text-transform:uppercase;">&gt;_ {_e(mode)} health check</p>
      <h1 class="h1" style="margin:0 0 14px 0;font-family:{_F};font-size:30px;font-weight:700;letter-spacing:-1px;line-height:1.1;color:{_AMBER};">{_e(newsletter_name)}</h1>
      {meta}
    </td></tr>
    <tr><td class="pad" style="background:{band_bg};padding:14px 32px;border-top:1px solid #222222;border-bottom:3px solid {status_color};">
      <p style="margin:0;font-family:{_F};font-size:14px;font-weight:700;letter-spacing:4px;text-transform:uppercase;color:{status_color};">
        {"&#9679;" if healthy else "&#9650;"}&nbsp; {status_text}</p>
    </td></tr>
    {note}
    {body_rows}
    <tr><td class="pad" style="background:{_D0};padding:20px 32px;text-align:center;">
      <p style="margin:0;font-family:{_F};font-size:11px;letter-spacing:2px;color:#6a6560;text-transform:uppercase;">
        Automated report &middot; {_e(newsletter_name)} &middot; sent every run as a heartbeat</p>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""



