# agents/widgets/bgg_hot.py
#
# The BGG hot list (board games): BoardGameGeek's XML API2 /hot list. Since July 2025 BGG needs a registered
# application and a Bearer token, and registration is free for non-commercial use only. So this widget:
#   - ships disabled: it runs only once the owner registers an app and stores the token as BGG_API_TOKEN;
#   - is commercial_use: False (owner decision 2026-10-16): never in a custom (premium) newsletter, never in the
#     studio catalog; at most in the free Tabletop Weekly profile.

import os
import xml.etree.ElementTree as ET

from . import ui
from .base import Widget, WidgetSkip
from .registry import register

HOT_URL = "https://boardgamegeek.com/xmlapi2/hot?type=boardgame"
GAME_URL = "https://boardgamegeek.com/boardgame/{id}"
TOKEN_ENV = "BGG_API_TOKEN"
TOP_N = 10
MAX_XML_BYTES = 200_000


def parse_hot(xml_text: str) -> list[dict]:
    if len(xml_text.encode("utf-8")) > MAX_XML_BYTES or "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise ValueError("unexpected BGG answer")   # no DTDs or entities: nothing to expand
    root = ET.fromstring(xml_text)
    rows = []
    for item in root.findall("item"):
        gid = item.get("id") or ""
        name_el = item.find("name")
        name = name_el.get("value") if name_el is not None else None
        if not gid.isdigit() or not name:
            continue
        year_el = item.find("yearpublished")
        year = year_el.get("value") if year_el is not None else None
        rank = item.get("rank")
        rows.append({"id": int(gid), "name": name.strip()[:100],
                     "year": int(year) if year and year.isdigit() else None,
                     "rank": int(rank) if rank and rank.isdigit() else len(rows) + 1})
        if len(rows) >= TOP_N:
            break
    return rows


@register
class BggHot(Widget):
    id = "bgg_hot"
    name = "The BGG hot list"
    description = "The board games BoardGameGeek members are looking at most this week."
    topics = ("boardgames",)
    commercial_use = False
    keys = (TOKEN_ENV,)

    def produce(self, ctx, config):
        token = os.environ[TOKEN_ENV].strip()
        rows = parse_hot(ctx.get(HOT_URL, headers={"Authorization": f"Bearer {token}"}).text)
        if not rows:
            raise WidgetSkip("empty hot list")
        return {"rows": rows, "as_of": ctx.now.isoformat()}

    def render(self, data, config, look):
        rows = [r for r in (data or {}).get("rows") or [] if isinstance(r, dict) and r.get("name")]
        if not rows:
            return ""
        table = [[r.get("rank"), ui.link(r["name"], GAME_URL.format(id=int(r["id"])) if isinstance(r.get("id"), int)
                                         else None), r.get("year")] for r in rows]
        body = ui.data_table(["#", "Game", "Year"], table, numeric=(2,))
        return ui.card(self.id, self.name, body, ui.accent_of(look), "🔥", None,
                       ui.attribution([("BoardGameGeek", "https://boardgamegeek.com")], data.get("as_of")))

    def sample_data(self):
        return {"as_of": "2026-10-18T16:00:00+00:00", "rows": [
            {"id": 100001, "name": "Example Game One", "year": 2026, "rank": 1},
            {"id": 100002, "name": "Example & Game Two", "year": 2025, "rank": 2},
            {"id": 100003, "name": "Example Game Three", "year": None, "rank": 3},
        ]}
