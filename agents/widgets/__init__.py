# agents/widgets/
#
# Widgets (contract C4): small data blocks next to the articles. Docs: docs/widgets.md.
#   base.py      Widget, ProduceContext           registry.py  register, get, catalog, for_topic
#   config.py    config validation and hash       runner.py    produce_edition_widgets (bounded)
#   store.py     Firestore / local storage        ui.py        email primitives (card, table, sparkline...)
#   plugin.py    the renderer plug-in (render_widget for agents/render)
# One module per widget below; importing this package registers them all.

from . import registry  # noqa: F401
from .base import ProduceContext, Widget, WidgetSkip  # noqa: F401

BUILTIN = (
    "paper_spotlight",
    "release_tracker",
    "free_games",
    "most_played",
    "deals",
    "trending_models",
    "rates_board",
    "week_ahead",
    "market_snapshot",
    "number_of_week",
    "crowdfunding_watch",
    "bgg_hot",
)

for _name in BUILTIN:
    __import__(f"{__name__}.{_name}")
