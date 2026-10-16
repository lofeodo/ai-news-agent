# agents/widgets/plugin.py
#
# The widget plug-in for the block renderer (agents/render/): render_widget(block, ctx) draws a widget block from
# the registry, with the data in ctx.content.widgets[widget id]. A widget with no data, or whose render raises,
# is left out with a warning; it never breaks the email. Importing this module also gives every widget that
# declares a `toc` entry its line in the table of contents (render.engine.WIDGET_TOC).

from render import engine

from . import registry

for _w in registry.all_widgets():
    if _w.toc:
        engine.WIDGET_TOC.setdefault(_w.id, _w.toc)


def render_widget(block, ctx) -> str:
    widget = registry.get(block.widget)
    if widget is None:
        ctx.warnings.append(f"widget {block.widget!r} left out: unknown widget")
        return ""
    data = ctx.content.widgets.get(block.widget)
    if data is None:
        ctx.warnings.append(f"widget {block.widget!r} left out: no data for it this week")
        return ""
    if widget.toc:
        engine.WIDGET_TOC.setdefault(widget.id, widget.toc)
    try:
        return widget.render(data, dict(block.config or {}), ctx.look) or ""
    except Exception as e:  # a widget must never break an email
        ctx.warnings.append(f"widget {block.widget!r} left out: render failed ({type(e).__name__})")
        return ""
