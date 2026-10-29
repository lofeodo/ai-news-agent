"""The site's self-hosted fonts must actually contain the letters the pages use.

Until 2026-10-27 six of the seven files in public/newsletter/fonts/ were the
wrong Google Fonts slice (no A-Z, a-z or digits), so every page silently fell
back to Arial, Georgia or Consolas. This test catches that kind of swap.
"""
import re
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

SITE = Path(__file__).resolve().parent.parent / "public" / "newsletter"
FONTS_CSS = SITE / "fonts.css"

# Basic Latin plus the French accents and punctuation the newsletter uses.
REQUIRED = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    ".,:;!?'\"()-&@/ "
    "éèêàâçôùûîïœÉÀ—–’“”…"
)


def _font_faces():
    """(family, weight, style, file) for each @font-face in fonts.css."""
    css = FONTS_CSS.read_text(encoding="utf-8")
    faces = []
    for block in re.findall(r"@font-face\s*\{(.*?)\}", css, re.S):
        family = re.search(r"font-family:\s*'([^']+)'", block).group(1)
        url = re.search(r"url\('/fonts/([^']+)'\)", block).group(1)
        weight = int(re.search(r"font-weight:\s*(\d+)", block).group(1))
        style = re.search(r"font-style:\s*(\w+)", block).group(1)
        faces.append((family, weight, style, SITE / "fonts" / url))
    return faces


FACES = _font_faces()


def test_fonts_css_lists_fonts():
    assert len(FACES) >= 3


@pytest.mark.parametrize("family,weight,style,path", FACES, ids=[f[3].name for f in FACES])
def test_font_file_covers_basic_latin(family, weight, style, path):
    font = TTFont(path)
    cmap = font.getBestCmap()
    missing = sorted(ch for ch in REQUIRED if ord(ch) not in cmap)
    assert not missing, f"{path.name} lacks {''.join(missing)!r}: wrong subset downloaded?"


@pytest.mark.parametrize("family,weight,style,path", FACES, ids=[f[3].name for f in FACES])
def test_font_file_matches_its_font_face(family, weight, style, path):
    font = TTFont(path)
    names = {n.nameID: n.toUnicode() for n in font["name"].names}
    assert names.get(1, "").startswith(family) or names.get(16, "") == family
    assert font["OS/2"].usWeightClass == weight
    assert bool(font["head"].macStyle & 2) == (style == "italic")


# The site is monospace plus Bebas Neue labels (docs/style-guide.md). A serif display face
# (Cormorant Garamond, EB Garamond) was defined for months without ever loading, and once it
# did it looked out of place everywhere it was used. Keep it out.
SERIF_RE = re.compile(r"Cormorant|EB Garamond|Garamond|Georgia|Times New Roman|var\(--display\)|var\(--body-serif\)")


@pytest.mark.parametrize("path", sorted(SITE.glob("*.css")) + sorted(SITE.glob("*.html")), ids=lambda p: p.name)
def test_site_never_uses_a_serif_display_font(path):
    src = path.read_text(encoding="utf-8")
    hits = sorted({m.group(0) for m in SERIF_RE.finditer(src)})
    assert not hits, f"{path.name} uses {hits}"
