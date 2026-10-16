# agents/render/shell.py
#
# The page around the blocks: <head> and styles, masthead with the table of contents, intro row and footer.
# Whitespace here is part of the golden files (tests/fixtures/render/), so edit it only for an intended change.

from .blocks import rule, toc_cell
from .palette import CHAR, CREAM, D0, D2, F, INK
from .text import esc

LOGO_BASE = "https://newsletter.lofeodo.com"   # absolute URL required for email logos


def logo_img(name_html: str) -> str:
    return (
        f'<img src="{LOGO_BASE}/images/logo-email.png" alt="{name_html}" width="48" height="48"'
        f' style="display:block;border:0;margin-bottom:14px;">'
    )


def toc_rows(entries: list[tuple[str, str]], accent: str) -> tuple[str, str]:
    """The two TOC rows (4 cells each, padded with blanks) for [(number, short label), ...]."""
    entries = list(entries)
    while len(entries) % 4 != 0:
        entries.append(("", ""))
    return ("".join(toc_cell(n, s, accent) for n, s in entries[:4]),
            "".join(toc_cell(n, s, accent) for n, s in entries[4:]))


def masthead(name: str, tagline: str, week_of: str, accent: str, logo_html: str,
             toc_row1: str, toc_row2: str) -> str:
    return f"""<!-- ════════════════════════════════════════════
         HEADER
         ════════════════════════════════════════════ -->
    <tr><td style="background:{D0};">
      <!-- amber top accent bar -->
      {rule(accent)}
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr><td class="mob-pad" style="padding:32px 40px 24px;">
        {logo_html}
        <p style="margin:0 0 6px 0;font-family:{F};font-size:12px;color:#484848;letter-spacing:5px;">&gt;_ TRANSMISSION</p>
        <h1 class="mob-h1" style="margin:0 0 0 0;font-family:{F};font-size:48px;font-weight:700;
                   letter-spacing:-1px;line-height:1;color:{accent};">{name}</h1>
        <table role="presentation" width="56" cellspacing="0" cellpadding="0" border="0" style="margin:12px 0 0;"><tr><td height="3" style="background:{accent};font-size:0;line-height:0;mso-line-height-rule:exactly;">&nbsp;</td></tr></table>
        {rule("#222222", "14px")}
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
               style="margin-top:12px;">
        <tr>
          <td style="vertical-align:bottom;">
            <p style="margin:0;font-family:{F};font-size:12px;color:#666666;
                      letter-spacing:2.5px;text-transform:uppercase;">
              {tagline}
            </p>
          </td>
          <td style="vertical-align:bottom;text-align:right;white-space:nowrap;">
            <p style="margin:0;font-family:{F};font-size:13px;color:#606060;">
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
    </td></tr>"""


def intro_row(intro: str, week_of: str, label: str = "EDITOR&apos;S NOTE") -> str:
    return f"""<!-- ════════════════════════════════════════════
         INTRO
         ════════════════════════════════════════════ -->
    <tr><td class="mob-pad" style="background:{CREAM};padding:28px 40px 26px;border-bottom:2px solid {D2};">
      <p style="margin:0 0 10px 0;font-family:{F};font-size:10px;color:#8a8070;letter-spacing:4px;">&gt;_ {label} &nbsp;&middot;&middot;&middot;&nbsp; {week_of}</p>
      <p style="margin:0 0 0 0;font-family:{F};font-size:15px;line-height:1.88;color:{INK};">{esc(intro)}</p>
    </td></tr>"""


def disclaimer_html(disclaimer: str) -> str:
    return f'<p style="margin:0 0 14px 0;font-family:{F};font-size:11px;color:{CHAR};">{esc(disclaimer)}</p>'


def address_html(mailing_address: str, accent: str) -> str:
    """CASL: the physical mailing address (from MAILING_ADDRESS)."""
    if not mailing_address:
        return ""
    return (
        f'<p style="margin:0 0 10px 0;font-family:{F};font-size:12px;color:{accent};">'
        f'{esc(mailing_address)}</p>'
    )


def footer(name: str, accent: str, address: str, disclaimers: str) -> str:
    return f"""<!-- ════════════════════════════════════════════
         FOOTER
         ════════════════════════════════════════════ -->
    <tr><td style="background:{D0};padding:0;">
      {rule("#1a1a1a")}
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
      <tr><td class="mob-pad" style="padding:24px 40px;text-align:center;">
        <p style="margin:0 0 4px 0;font-family:{F};font-size:11px;color:{accent};
                  letter-spacing:3px;text-transform:uppercase;">{name}</p>
        <p style="margin:0 0 14px 0;font-family:{F};font-size:12px;color:{accent};">
          You're receiving this because you subscribed.
        </p>
        {address}{disclaimers}
        <p style="margin:0;font-family:{F};font-size:12px;">
          <a href="{{{{UNSUBSCRIBE_URL}}}}" style="color:{accent};text-decoration:underline;">Unsubscribe</a>
          <span style="color:{accent};">&nbsp;&nbsp;·&nbsp;&nbsp;</span>
          <a href="{{{{PREFERENCES_URL}}}}" style="color:{accent};text-decoration:underline;">Manage preferences</a>
        </p>
      </td></tr>
      </table>
    </td></tr>"""


def page(name: str, preheader: str, inner: str) -> str:
    """The whole document; `inner` is every row of the 640px column, header to footer."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>{name}</title>
  <!--[if mso]><xml><o:OfficeDocumentSettings><o:AllowPNG/><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]-->
  <style>
    @media (prefers-color-scheme: dark) {{
      .dm-outer {{ background:#060606 !important; }}
    }}
    @media only screen and (max-width: 480px) {{
      .mob-pad {{ padding-left: 16px !important; padding-right: 16px !important; }}
      .mob-share {{ padding-left: 12px !important; padding-right: 12px !important; }}
      .mob-share td {{ font-size: 10px !important; }}
      .mob-share-in {{ padding-left: 12px !important; }}
      .mob-share-btn {{ padding: 6px 10px !important; letter-spacing: 1px !important; }}
      .mob-hide {{ display: none !important; }}
      .mob-h1 {{ font-size: 26px !important; }}
      .mob-spot-title {{ font-size: 21px !important; }}
      .mob-btn {{ display: block !important; text-align: center; }}
      .mob-block {{ display: block !important; width: 100% !important; }}
      .mob-hn {{ border-left: none !important; padding-left: 0 !important; margin-top: 10px; }}
      .mob-toc-cell {{ width: 50% !important; display: inline-block !important; box-sizing: border-box; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">

<!-- preheader: inbox preview text = editor's note; hidden in the body -->
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#ffffff;opacity:0;">{esc(preheader[:200])}{'&zwnj;&nbsp;' * 60}</div>

<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
<tr><td class="dm-outer" style="padding:32px 12px 48px;">

  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
         style="max-width:640px;margin:0 auto;">

    {inner}

  </table>

</td></tr>
</table>
</body>
</html>"""
