# agents/render/palette.py
#
# Colours and fonts of the email. Design: dark editorial dispatch — industrial zine meets hacker terminal.

F     = "'Menlo','Cascadia Mono','Consolas','Courier New',monospace"
# Darks
D0    = "#080808"   # header / footer (deepest)
D1    = "#111111"   # page outer bg / research section
D2    = "#191919"   # section number strips
D3    = "#222222"   # paper card bg
# Lights
WHITE = "#ffffff"   # article content
CREAM = "#faf9f4"   # intro (barely warm)
# Text
INK   = "#111111"   # body text on white
INK2  = "#555555"   # secondary text on white
ASH   = "#d4cfc8"   # text on dark
CHAR  = "#6a6560"   # secondary text on dark
# Accents
AMBER = "#c8b89a"   # brand amber (soft, warm)
GOLD  = "#d4a843"   # bright gold for score numbers
HN    = "#22c55e"   # Hacker News score green
SEPR  = "#ededed"   # separator on white

AI_LINK = "#7a6548"  # the AI newsletter's link colour on white cards


def darken(hex_color: str, factor: float = 0.5) -> str:
    """A darker shade of #rrggbb, for link text on a white card (the accent itself is meant for dark backgrounds)."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#{:02x}{:02x}{:02x}".format(int(r * factor), int(g * factor), int(b * factor))
