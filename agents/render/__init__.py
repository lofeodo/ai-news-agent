# agents/render/
#
# The block renderer: turns a newsletter definition (agents/newsletter_def.py) plus content into email-safe HTML.
#   engine.py   render(): the block loop     palette.py  colours and fonts
#   blocks.py   cards, strips, share         shell.py    head, masthead + TOC, intro row, footer
#   text.py     escaping                     from_editions.py  render_from_editions(): custom newsletters

from .engine import Content, Look, RenderContext, RenderResult, default_render_widget, render  # noqa: F401
from .from_editions import SIZE_WARNING_BYTES, render_from_editions  # noqa: F401
