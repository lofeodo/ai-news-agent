# agents/widgets/free_games.py
#
# Free this week (video games): games free to keep on the Epic Games Store now and next, from Epic's public but
# undocumented freeGamesPromotions feed, parsed defensively. If Epic fails or its payload changes shape, the
# GamerPower API (free for commercial use with an attribution link) supplies current PC game giveaways instead.

from datetime import datetime, timezone

from . import ui
from .base import Widget, WidgetSkip
from .registry import register

EPIC_URL = ("https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
            "?locale=en-US&country=CA&allowCountries=CA")
EPIC_STORE = "https://store.epicgames.com/en-US"
GAMERPOWER_URL = "https://www.gamerpower.com/api/giveaways?platform=pc&type=game&sort-by=popularity"
MAX_ITEMS = 5


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            t = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _free_offers(group_list):
    """Every 100%-off offer window in a promotions list: [(start, end), ...]."""
    out = []
    for group in group_list if isinstance(group_list, list) else []:
        offers = group.get("promotionalOffers") if isinstance(group, dict) else None
        for o in offers if isinstance(offers, list) else []:
            if not isinstance(o, dict):
                continue
            setting = o.get("discountSetting") or {}
            if setting.get("discountPercentage") == 0:   # Epic encodes "free" as 0% of the price left
                start, end = _time(o.get("startDate")), _time(o.get("endDate"))
                if start and end:
                    out.append((start, end))
    return out


def _epic_url(e: dict) -> str:
    slug = None
    for m in (e.get("catalogNs") or {}).get("mappings") or []:
        if isinstance(m, dict) and m.get("pageSlug"):
            slug = m["pageSlug"]
            break
    slug = slug or (e.get("productSlug") or "").split("/")[0] or None
    return f"{EPIC_STORE}/p/{slug}" if slug else f"{EPIC_STORE}/free-games"


def parse_epic(payload, now: datetime) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Epic payload is not an object")
    catalog = (payload.get("data") or {}).get("Catalog") or {}
    els = (catalog.get("searchStore") or {}).get("elements")
    if not isinstance(els, list):
        raise ValueError("Epic payload has no element list")
    free_now, upcoming = [], []
    for e in els:
        if not isinstance(e, dict) or not isinstance(e.get("title"), str):
            continue
        promos = e.get("promotions") or {}
        if not isinstance(promos, dict):
            continue
        item = {"title": e["title"][:120], "url": _epic_url(e)}
        for start, end in _free_offers(promos.get("promotionalOffers")):
            if start <= now < end:
                free_now.append({**item, "until": end.isoformat()})
                break
        else:
            for start, end in _free_offers(promos.get("upcomingPromotionalOffers")):
                if start > now:
                    upcoming.append({**item, "from": start.isoformat(), "until": end.isoformat()})
                    break
    return {"source": "epic", "now": free_now[:MAX_ITEMS], "upcoming": upcoming[:MAX_ITEMS]}


def parse_gamerpower(payload, now: datetime) -> dict:
    if not isinstance(payload, list):
        raise ValueError("GamerPower payload is not a list")
    items = []
    for g in payload:
        if not isinstance(g, dict) or not isinstance(g.get("title"), str) or g.get("status", "Active") != "Active":
            continue
        end = _time(g.get("end_date"))
        if end and end <= now:
            continue
        title = g["title"].removesuffix(" Giveaway")[:120]
        items.append({"title": title, "url": g.get("open_giveaway_url") or g.get("gamerpower_url"),
                      "until": end.isoformat() if end else None, "where": str(g.get("platforms") or "")[:60]})
        if len(items) >= MAX_ITEMS:
            break
    return {"source": "gamerpower", "now": items, "upcoming": []}


@register
class FreeGames(Widget):
    id = "free_games"
    name = "Free this week"
    description = "Games you can claim for free and keep, now and coming next."
    topics = ("gaming",)

    def produce(self, ctx, config):
        data = None
        try:
            data = parse_epic(ctx.get_json(EPIC_URL), ctx.now)
        except Exception as e:
            print(f"  [free_games] Epic feed unusable ({type(e).__name__}: {str(e)[:120]}), trying GamerPower",
                  flush=True)
        if not data or not data["now"]:
            data = parse_gamerpower(ctx.get_json(GAMERPOWER_URL), ctx.now)
        if not data["now"] and not data["upcoming"]:
            raise WidgetSkip("no free games found")
        data["as_of"] = ctx.now.isoformat()
        return data

    def render(self, data, config, look):
        data = data if isinstance(data, dict) else {}
        rows = []
        for g in data.get("now") or []:
            if isinstance(g, dict) and g.get("title"):
                when = ui.fmt_date(g.get("until"))
                meta = " · ".join(x for x in (g.get("where"), f"free until {when}" if when else "free now") if x)
                rows.append((ui.link(g["title"], g.get("url")), meta))
        for g in data.get("upcoming") or []:
            if isinstance(g, dict) and g.get("title"):
                start, end = ui.fmt_date(g.get("from")), ui.fmt_date(g.get("until"))
                rows.append((ui.Raw(ui.link(g["title"], g.get("url")) + ui.badge("NEXT", ui.FLAT)),
                             f"free from {start} to {end}" if start and end else "coming soon"))
        if not rows:
            return ""
        if data.get("source") == "gamerpower":
            source = [("GamerPower", "https://www.gamerpower.com")]
        else:
            source = [("Epic Games Store", f"{EPIC_STORE}/free-games")]
        return ui.card(self.id, self.name, ui.item_list(rows), ui.accent_of(look), "🎁", None,
                       ui.attribution(source, data.get("as_of"), "Prices and dates can change; check the store."))

    def sample_data(self):
        return {"source": "epic", "as_of": "2026-10-18T16:00:00+00:00",
                "now": [{"title": "Bad Cheese", "url": f"{EPIC_STORE}/p/bad-cheese-4256d9",
                         "until": "2026-10-22T15:00:00+00:00"},
                        {"title": "Agent A: A puzzle in disguise", "url": f"{EPIC_STORE}/p/agent-a",
                         "until": "2026-10-22T15:00:00+00:00"}],
                "upcoming": [{"title": "Sker Ritual", "url": f"{EPIC_STORE}/p/sker-ritual",
                              "from": "2026-10-22T15:00:00+00:00", "until": "2026-10-29T15:00:00+00:00"}]}
