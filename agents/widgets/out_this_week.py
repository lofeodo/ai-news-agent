# agents/widgets/out_this_week.py
#
# Out this week (video games): games releasing in the send week, most anticipated first, from the RAWG API.
# RAWG needs a key (owner registers, stores it as RAWG_API_KEY) and its free plan is non-commercial with an
# attribution link on every page that uses the data, so this widget ships disabled and is commercial_use: False.
# RAWG takes the key in the URL, and a requests error repeats the URL, so errors are re-raised without it.

import os
import re
from datetime import date, timedelta

from . import ui
from .base import Widget, WidgetSkip
from .registry import register

API_URL = "https://api.rawg.io/api/games"
GAME_URL = "https://rawg.io/games/{slug}"
KEY_ENV = "RAWG_API_KEY"
TOP_N = 8
_SLUG = re.compile(r"^[a-z0-9][a-z0-9\-]{0,120}$")


@register
class OutThisWeek(Widget):
    id = "out_this_week"
    name = "Out this week"
    description = "The most anticipated video games releasing this week, and on which platforms."
    topics = ("gaming",)
    commercial_use = False
    keys = (KEY_ENV,)

    def produce(self, ctx, config):
        try:
            start = date.fromisoformat(ctx.week)
        except (TypeError, ValueError):
            start = ctx.now.date()
        end = start + timedelta(days=6)
        params = {"key": os.environ[KEY_ENV].strip(), "dates": f"{start.isoformat()},{end.isoformat()}",
                  "ordering": "-added", "page_size": 20}
        try:
            payload = ctx.get_json(API_URL, params=params)
        except Exception as e:  # never let the key (in the URL) reach a log, the run doc or the health check
            status = getattr(getattr(e, "response", None), "status_code", None)
            raise RuntimeError(f"RAWG request failed ({type(e).__name__}{f', HTTP {status}' if status else ''})") \
                from None
        rows = []
        for g in (payload or {}).get("results") or []:
            if not isinstance(g, dict) or not isinstance(g.get("name"), str) or not g.get("released"):
                continue
            slug = g.get("slug") if isinstance(g.get("slug"), str) and _SLUG.match(g["slug"]) else None
            plats = [p["platform"]["name"] for p in g.get("platforms") or []
                     if isinstance(p, dict) and isinstance(p.get("platform"), dict) and p["platform"].get("name")]
            rows.append({"name": g["name"].strip()[:100], "released": str(g["released"])[:10], "slug": slug,
                         "platforms": [str(x)[:30] for x in plats[:3]]})
            if len(rows) >= TOP_N:
                break
        if not rows:
            raise WidgetSkip("no releases this week")
        return {"rows": rows, "week_start": start.isoformat(), "as_of": ctx.now.isoformat()}

    def render(self, data, config, look):
        rows = [r for r in (data or {}).get("rows") or [] if isinstance(r, dict) and r.get("name")]
        if not rows:
            return ""
        items = []
        for r in rows:
            slug = r.get("slug") if isinstance(r.get("slug"), str) and _SLUG.match(r["slug"]) else None
            title = ui.link(r["name"], GAME_URL.format(slug=slug)) if slug else r["name"]
            when = ui.fmt_date(r.get("released"))
            plats = ", ".join(str(p) for p in r.get("platforms") or [] if p)
            items.append((title, " · ".join(x for x in (when, plats) if x)))
        return ui.card(self.id, self.name, ui.item_list(items), ui.accent_of(look), "🕹", None,
                       ui.attribution([("RAWG", "https://rawg.io")], data.get("as_of")))

    def sample_data(self):
        return {"week_start": "2026-10-19", "as_of": "2026-10-18T16:00:00+00:00", "rows": [
            {"name": "Example Release One", "released": "2026-10-20", "slug": "example-release-one",
             "platforms": ["PC", "PlayStation 5"]},
            {"name": "Example Release Two", "released": "2026-10-22", "slug": "example-release-two",
             "platforms": ["Nintendo Switch"]},
        ]}
