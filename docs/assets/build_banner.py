"""Builds docs/assets/banner.svg: the README hero, in the site's own palette and fonts.

Fonts are embedded as base64 @font-face so the SVG renders the same inside a GitHub <img>, which cannot load
external fonts. Run from the repo root:  python docs/assets/build_banner.py
"""
import base64
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "public" / "newsletter" / "fonts"
OUT = Path(__file__).resolve().parent / "banner.svg"

W, H = 1280, 420
SAND, INK, TEXT, MUTED = "#c8b89a", "#0f0f0f", "#e8e8e8", "#8b8b8b"


def font_face(family, filename, style="normal", weight=400):
    data = base64.b64encode((FONTS / filename).read_bytes()).decode()
    return (f"@font-face{{font-family:'{family}';font-style:{style};font-weight:{weight};"
            f"src:url(data:font/woff2;base64,{data}) format('woff2');}}")


def latent_field():
    """A deterministic scatter of nodes and links on the right: a nod to 'latent space'."""
    pts = []
    for i in range(34):
        a = i * 2.399963  # golden angle
        r = 36 * math.sqrt(i + 1) * 1.55
        pts.append((960 + r * math.cos(a) * 1.15, 200 + r * math.sin(a) * 0.9))
    pts = [(x, y) for x, y in pts if 700 < x < W - 10 and 12 < y < H - 12]
    parts = []
    for i, (x1, y1) in enumerate(pts):
        for x2, y2 in pts[i + 1:]:
            d = math.hypot(x1 - x2, y1 - y2)
            if d < 105:
                parts.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
                             f'stroke="{SAND}" stroke-opacity="{0.34 - d / 400:.2f}" stroke-width="1"/>')
    for i, (x, y) in enumerate(pts):
        r = 2.2 + (i % 4) * 0.9
        parts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r:.1f}" fill="{SAND}" fill-opacity="{0.35 + (i % 3) * 0.2:.2f}"/>')
    return "\n    ".join(parts)


def chip(x, label, width):
    return (f'<g transform="translate({x},294)"><rect width="{width}" height="38" rx="19" fill="none" '
            f'stroke="{SAND}" stroke-opacity="0.55"/><text x="{width / 2}" y="24.5" text-anchor="middle" '
            f'class="chip">{label}</text></g>')


svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="Latent SpaceMail: a weekly AI briefing, written and sent by a team of AI agents. Live site newsletter.lofeodo.com">
  <title>Latent SpaceMail</title>
  <defs>
    <style>
      {font_face("Cormorant Garamond", "cormorant-garamond-600.woff2", weight=600)}
      {font_face("Cormorant Garamond", "cormorant-garamond-400i.woff2", style="italic")}
      {font_face("IBM Plex Mono", "ibm-plex-mono-400.woff2")}
      .title{{font:600 104px 'Cormorant Garamond',Georgia,serif;fill:{SAND};letter-spacing:1px}}
      .tag{{font:italic 400 34px 'Cormorant Garamond',Georgia,serif;fill:{TEXT}}}
      .chip{{font:400 14.5px 'IBM Plex Mono',Menlo,Consolas,monospace;fill:{SAND};letter-spacing:1.2px}}
      .cta{{font:400 17px 'IBM Plex Mono',Menlo,Consolas,monospace;fill:{INK};letter-spacing:1px;font-weight:700}}
    </style>
    <radialGradient id="glow" cx="78%" cy="45%" r="55%">
      <stop offset="0" stop-color="{SAND}" stop-opacity="0.20"/>
      <stop offset="1" stop-color="{SAND}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="{W}" height="{H}" rx="18" fill="{INK}"/>
  <rect width="{W}" height="{H}" rx="18" fill="url(#glow)"/>
  <rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="18" fill="none" stroke="#2a2a2a"/>
  <g>
    {latent_field()}
  </g>
  <g transform="translate(80,26)">
    <rect x="0" y="0" width="20" height="70" fill="{SAND}"/>
    <rect x="20" y="50" width="50" height="20" fill="{SAND}"/>
    <circle cx="45" cy="25" r="20" fill="none" stroke="{SAND}" stroke-width="3"/>
  </g>
  <text x="76" y="204" class="title">Latent SpaceMail</text>
  {chip(80, "MULTI-AGENT ORCHESTRATION", 300)}
  {chip(394, "LIVE MONITORING", 196)}
  {chip(604, "MEASURED EVALS", 186)}
  {chip(804, "CLOUD + CI", 140)}
  <text x="80" y="260" class="tag">A weekly AI briefing, written and sent by a team of AI agents.</text>
  <a href="https://newsletter.lofeodo.com">
    <rect x="80" y="352" width="440" height="46" rx="23" fill="{SAND}"/>
    <text x="300" y="381" text-anchor="middle" class="cta">LIVE SITE  →  newsletter.lofeodo.com</text>
  </a>
</svg>
"""
OUT.write_text(svg, encoding="utf-8")
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KiB)")
