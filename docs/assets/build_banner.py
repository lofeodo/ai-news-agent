"""Builds docs/assets/banner-dark.svg and banner-light.svg: the README hero, in the site's own palette and fonts.

The README picks one with <picture> and prefers-color-scheme, so the banner matches the reader's GitHub theme.
Fonts are embedded as base64 @font-face so the SVG renders the same inside a GitHub <img>, which cannot load
external fonts. Run from the repo root:  python docs/assets/build_banner.py
"""
import base64
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "public" / "newsletter" / "fonts"
OUT_DIR = Path(__file__).resolve().parent

W, H = 1280, 576
PAD = 104   # left padding; the same distance is kept from the top and the bottom

THEMES = {
    "dark": dict(bg="#0f0f0f", border="#2a2a2a", title="#c8b89a", tag="#e8e8e8", mark="#c8b89a",
                 net="#c8b89a", net_a=1.0, glow="#c8b89a", glow_a=0.20,
                 pill_a="#e6d6b4", pill_b="#c8b89a", pill_text="#0f0f0f", pill_ring="#c8b89a",
                 shadow="#c8b89a", shadow_a=0.35, live="#1f9d4d",
                 chip_line="#c8b89a", chip_text="#c8b89a"),
    "light": dict(bg="#fbf9f4", border="#e3dccd", title="#14110d", tag="#3d372e", mark="#14110d",
                  net="#8a7658", net_a=0.9, glow="#c8b89a", glow_a=0.30,
                  pill_a="#2a2520", pill_b="#0f0f0f", pill_text="#f1e6cf", pill_ring="#0f0f0f",
                  shadow="#0f0f0f", shadow_a=0.28, live="#3fd477",
                  chip_line="#8a7658", chip_text="#5c4a2e"),
}


def font_face(family, filename, style="normal", weight=400):
    data = base64.b64encode((FONTS / filename).read_bytes()).decode()
    return (f"@font-face{{font-family:'{family}';font-style:{style};font-weight:{weight};"
            f"src:url(data:font/woff2;base64,{data}) format('woff2');}}")


def latent_field(t):
    """A deterministic scatter of nodes and links on the right: a nod to 'latent space'."""
    pts = []
    for i in range(34):
        a = i * 2.399963  # golden angle
        r = 36 * math.sqrt(i + 1) * 1.55
        pts.append((1000 + r * math.cos(a) * 1.15, 232 + r * math.sin(a) * 0.9))
    pts = [(x, y) for x, y in pts if 760 < x < W - 36 and 36 < y < H - 36]
    parts = []
    for i, (x1, y1) in enumerate(pts):
        for x2, y2 in pts[i + 1:]:
            d = math.hypot(x1 - x2, y1 - y2)
            if d < 105:
                parts.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" stroke="{t["net"]}" '
                             f'stroke-opacity="{(0.34 - d / 400) * t["net_a"]:.2f}" stroke-width="1"/>')
    for i, (x, y) in enumerate(pts):
        r = 2.2 + (i % 4) * 0.9
        parts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r:.1f}" fill="{t["net"]}" '
                     f'fill-opacity="{(0.35 + (i % 3) * 0.2) * t["net_a"]:.2f}"/>')
    return "\n    ".join(parts)


CHIPS = ("MULTI-AGENT ORCHESTRATION", "LIVE MONITORING", "MEASURED EVALS", "CLOUD + CI")


def chips(t, y):
    """Small outlined pills describing what the project does; deliberately quiet next to the live-site pill."""
    out, x = [], PAD
    for label in CHIPS:
        w = len(label) * 10.4 + 40
        out.append(f'<g transform="translate({x:.0f},{y})"><rect width="{w:.0f}" height="36" rx="18" fill="none" '
                   f'stroke="{t["chip_line"]}" stroke-opacity="0.7"/><text x="{w / 2:.0f}" y="23.5" text-anchor="middle" '
                   f'class="chip">{label}</text></g>')
        x += w + 14
    return "\n  ".join(out)


def build(name):
    t = THEMES[name]
    pill_w, pill_h, pill_y = 800, 92, 428
    pill_cx, pill_cy = PAD + pill_w / 2, pill_y + pill_h / 2
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="Latent SpaceMail: a weekly AI briefing, written and sent by a team of AI agents. Live site newsletter.lofeodo.com">
  <title>Latent SpaceMail</title>
  <defs>
    <style>
      {font_face("Cormorant Garamond", "cormorant-garamond-600.woff2", weight=600)}
      {font_face("Cormorant Garamond", "cormorant-garamond-400i.woff2", style="italic")}
      {font_face("IBM Plex Mono", "ibm-plex-mono-400.woff2")}
      .title{{font:600 108px 'Cormorant Garamond',Georgia,serif;fill:{t["title"]};letter-spacing:1px}}
      .tag{{font:italic 400 36px 'Cormorant Garamond',Georgia,serif;fill:{t["tag"]}}}
      .chip{{font:400 15.5px 'IBM Plex Mono',Menlo,Consolas,monospace;fill:{t["chip_text"]};letter-spacing:1.3px}}
      .cta{{font:400 31px 'IBM Plex Mono',Menlo,Consolas,monospace;fill:{t["pill_text"]};letter-spacing:1.2px;font-weight:700}}
    </style>
    <radialGradient id="glow" cx="78%" cy="48%" r="55%">
      <stop offset="0" stop-color="{t["glow"]}" stop-opacity="{t["glow_a"]}"/>
      <stop offset="1" stop-color="{t["glow"]}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="pill" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{t["pill_a"]}"/>
      <stop offset="1" stop-color="{t["pill_b"]}"/>
    </linearGradient>
    <filter id="drop" x="-20%" y="-60%" width="140%" height="240%">
      <feGaussianBlur stdDeviation="14"/>
    </filter>
  </defs>
  <rect width="{W}" height="{H}" rx="20" fill="{t["bg"]}"/>
  <rect width="{W}" height="{H}" rx="20" fill="url(#glow)"/>
  <rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="20" fill="none" stroke="{t["border"]}"/>
  <g>
    {latent_field(t)}
  </g>
  <g transform="translate({PAD},64)">
    <rect x="0" y="0" width="20" height="70" fill="{t["mark"]}"/>
    <rect x="20" y="50" width="50" height="20" fill="{t["mark"]}"/>
    <circle cx="45" cy="25" r="20" fill="none" stroke="{t["mark"]}" stroke-width="3"/>
  </g>
  <text x="{PAD - 4}" y="256" class="title">Latent SpaceMail</text>
  <text x="{PAD}" y="316" class="tag">A weekly AI briefing, written and sent by a team of AI agents.</text>
  {chips(t, 356)}
  <a href="https://newsletter.lofeodo.com">
    <rect x="{PAD}" y="{pill_y + 10}" width="{pill_w}" height="{pill_h}" rx="{pill_h / 2}" fill="{t["shadow"]}" fill-opacity="{t["shadow_a"]}" filter="url(#drop)"/>
    <rect x="{PAD - 6}" y="{pill_y - 6}" width="{pill_w + 12}" height="{pill_h + 12}" rx="{(pill_h + 12) / 2}" fill="none" stroke="{t["pill_ring"]}" stroke-opacity="0.45" stroke-width="2"/>
    <rect x="{PAD}" y="{pill_y}" width="{pill_w}" height="{pill_h}" rx="{pill_h / 2}" fill="url(#pill)"/>
    <circle cx="{PAD + 54}" cy="{pill_cy}" r="17" fill="{t["live"]}" fill-opacity="0.25"/>
    <circle cx="{PAD + 54}" cy="{pill_cy}" r="9" fill="{t["live"]}"/>
    <text x="{pill_cx + 28:.0f}" y="{pill_cy + 11:.0f}" text-anchor="middle" class="cta">LIVE SITE  →  newsletter.lofeodo.com</text>
  </a>
</svg>
"""
    out = OUT_DIR / f"banner-{name}.svg"
    out.write_text(svg, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    for theme in THEMES:
        build(theme)
