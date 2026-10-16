# agents/widgets/most_played.py
#
# Most played on Steam (video games): Steam's weekly most-played chart (ISteamChartsService, no key) with each
# game's name from the store's appdetails endpoint. Steam's Web API terms license data "for personal use via your
# Application" and say nothing explicit about commercial use, so until the owner has read them this widget is
# marked commercial_use: False (free ready-made newsletters only, never premium). docs/widgets.md#sources.

from datetime import datetime, timezone

from . import ui
from .base import Widget, WidgetSkip
from .registry import register

CHART_URL = "https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/"
APP_URL = "https://store.steampowered.com/api/appdetails?appids={appid}&filters=basic"
STORE_APP = "https://store.steampowered.com/app/{appid}"
TOP_N = 8          # ten rows came to just over the 8 KB fragment cap
NAME_TIMEOUT = 5


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


@register
class MostPlayed(Widget):
    id = "most_played"
    name = "Most played on Steam"
    description = "The games with the most players on Steam this week, and how they moved."
    topics = ("gaming",)
    commercial_use = False
    max_age_hours = 8 * 24     # Steam rolls the chart up weekly

    def produce(self, ctx, config):
        resp = (ctx.get_json(CHART_URL) or {}).get("response") or {}
        ranks = resp.get("ranks")
        if not isinstance(ranks, list) or not ranks:
            raise ValueError("Steam chart has no ranks")
        rows = []
        for r in ranks:
            if len(rows) >= TOP_N:
                break
            appid = _int((r or {}).get("appid")) if isinstance(r, dict) else None
            if appid is None:
                continue
            try:
                info = ctx.get_json(APP_URL.format(appid=appid), timeout=NAME_TIMEOUT).get(str(appid)) or {}
            except Exception:
                continue
            name = (info.get("data") or {}).get("name") if info.get("success") else None
            if not isinstance(name, str) or not name.strip():
                continue
            rows.append({"rank": _int(r.get("rank")), "appid": appid, "name": name.strip()[:80],
                         "last_week_rank": _int(r.get("last_week_rank")), "peak": _int(r.get("peak_in_game"))})
        if len(rows) < 3:
            raise WidgetSkip("too few named games in the chart")
        rollup = _int(resp.get("rollup_date"))
        as_of = datetime.fromtimestamp(rollup, timezone.utc) if rollup else ctx.now
        return {"rows": rows, "as_of": as_of.isoformat()}

    def render(self, data, config, look):
        rows = [r for r in (data or {}).get("rows") or [] if isinstance(r, dict) and r.get("name")]
        if not rows:
            return ""
        table = []
        for r in rows:
            rank, last = _int(r.get("rank")), _int(r.get("last_week_rank"))
            if last and rank:
                move = ui.delta(last - rank, 0, up_good=True)
            else:
                move = ui.Raw(ui.badge("NEW", ui.UP))
            peak = _int(r.get("peak"))
            table.append([rank, ui.link(r["name"], STORE_APP.format(appid=_int(r.get("appid")) or 0)),
                          f"{peak:,}" if peak else None, move])
        body = ui.data_table(["#", "Game", "Peak players", "Move"], table, numeric=(2, 3))
        return ui.card(self.id, self.name, body, ui.accent_of(look), "🎮", None,
                       ui.attribution([("Steam", "https://store.steampowered.com/charts/mostplayed")],
                                      data.get("as_of"), "Move = places up or down since last week."))

    def sample_data(self):
        return {"as_of": "2026-10-15T00:00:00+00:00", "rows": [
            {"rank": 1, "appid": 730, "name": "Counter-Strike 2", "last_week_rank": 1, "peak": 1165887},
            {"rank": 2, "appid": 570, "name": "Dota 2", "last_week_rank": 2, "peak": 874030},
            {"rank": 3, "appid": 578080, "name": "PUBG: BATTLEGROUNDS", "last_week_rank": 3, "peak": 789961},
            {"rank": 4, "appid": 1422450, "name": "Deadlock", "last_week_rank": 6, "peak": 198239},
            {"rank": 5, "appid": 3393110, "name": "AION 2", "last_week_rank": 0, "peak": 292931},
        ]}
