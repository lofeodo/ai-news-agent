# agents/widgets/market_snapshot.py
#
# The week in markets (finance): exchange rates and government bond yields from open-licence sources only
# (Bank of Canada Valet, US Treasury), each with its change over the week and a five-day text sparkline. Stock
# indices are exchange-licensed and need an owner decision on a licensed source (docs/widgets.md#sources), so
# they are not here. A series that is missing or older than a week is one dropped row, not a dropped widget.

from datetime import date, timedelta

from . import finance_data as fd
from . import ui
from .base import Widget
from .registry import register

# (key, label, source, series id, kind)  kind "fx" = 4 decimals, "yield" = percent with changes in basis points
SERIES = (
    ("usdcad", "US dollar in CAD", "boc", "FXUSDCAD", "fx"),
    ("eurcad", "Euro in CAD", "boc", "FXEURCAD", "fx"),
    ("ca2y", "Canada 2-year yield", "boc", "BD.CDN.2YR.DQ.YLD", "yield"),
    ("ca10y", "Canada 10-year yield", "boc", "BD.CDN.10YR.DQ.YLD", "yield"),
    ("us2y", "US 2-year yield", "treasury", "2 Yr", "yield"),
    ("us10y", "US 10-year yield", "treasury", "10 Yr", "yield"),
)
MAX_ROW_AGE_DAYS = 7
SPARK_POINTS = 5


def _send_day(ctx) -> date:
    try:
        return date.fromisoformat(ctx.week)
    except (TypeError, ValueError):
        return ctx.now.date()


@register
class MarketSnapshot(Widget):
    id = "market_snapshot"
    name = "The week in markets"
    description = "The Canadian dollar and Canadian and US government bond yields: where they ended the week."
    topics = ("finance",)
    max_age_hours = 7 * 24

    def produce(self, ctx, config):
        today = _send_day(ctx)
        points: dict = {}
        try:
            boc = fd.boc_series(ctx, [s[3] for s in SERIES if s[2] == "boc"], 10)
            points.update({key: boc.get(sid) for key, _, src, sid, _ in SERIES if src == "boc"})
        except Exception as e:
            print(f"  [market_snapshot] Bank of Canada failed: {type(e).__name__}", flush=True)
        try:
            tr = fd.treasury_yields(ctx, today.year, [s[3] for s in SERIES if s[2] == "treasury"])
            if today.month == 1 and min(len(v) for v in tr.values() or [[]]) < SPARK_POINTS + 2:
                prev = fd.treasury_yields(ctx, today.year - 1, [s[3] for s in SERIES if s[2] == "treasury"])
                tr = {k: prev.get(k, []) + tr.get(k, []) for k in set(prev) | set(tr)}
            points.update({key: tr.get(sid) for key, _, src, sid, _ in SERIES if src == "treasury"})
        except Exception as e:
            print(f"  [market_snapshot] US Treasury failed: {type(e).__name__}", flush=True)

        oldest_ok = (today - timedelta(days=MAX_ROW_AGE_DAYS)).isoformat()
        rows = []
        for key, label, src, _, kind in SERIES:
            pts = points.get(key) or []
            if not pts or pts[-1][0] < oldest_ok:
                continue
            change = fd.change_over(pts, 7, today - timedelta(days=1))
            rows.append({"key": key, "label": label, "source": src, "kind": kind, "date": pts[-1][0],
                         "value": pts[-1][1], "change": change,
                         "spark": [v for _, v in pts[-SPARK_POINTS:]]})
        if not rows:
            raise RuntimeError("no market series could be fetched")
        return {"rows": rows, "as_of": max(r["date"] for r in rows)}

    def render(self, data, config, look):
        rows = [r for r in (data or {}).get("rows") or [] if isinstance(r, dict) and r.get("label")]
        table, used = [], []
        for r in rows:
            v, ch = r.get("value"), r.get("change")
            if not isinstance(v, (int, float)):
                continue
            if r.get("kind") == "fx":
                level, move = f"{v:.4f}", ui.delta(ch, 4, up_good=None)
            else:
                level = f"{v:.2f}%"
                move = ui.delta(ch * 100 if isinstance(ch, (int, float)) else None, 0, " bp", up_good=None)
            table.append([r["label"], level, move, ui.spark_html(r.get("spark") or [], label="last five days")])
            if r.get("source") in fd.SOURCES and fd.SOURCES[r["source"]] not in used:
                used.append(fd.SOURCES[r["source"]])
        if not table:
            return ""
        body = ui.data_table(["Market", "Level", "Week", "5 days"], table, numeric=(1, 2, 3))
        return ui.card(self.id, self.name, body, ui.accent_of(look), "📈", None,
                       ui.attribution(used, data.get("as_of"),
                                      fd.NOT_ADVICE + ". Weekly change computed by us."))

    def sample_data(self):
        return {"as_of": "2026-10-15", "rows": [
            {"key": "usdcad", "label": "US dollar in CAD", "source": "boc", "kind": "fx", "date": "2026-10-15",
             "value": 1.4196, "change": -0.0061, "spark": [1.4271, 1.4222, 1.4263, 1.4196, 1.4196]},
            {"key": "ca10y", "label": "Canada 10-year yield", "source": "boc", "kind": "yield", "date": "2026-10-15",
             "value": 3.48, "change": 0.05, "spark": [3.41, 3.43, 3.46, 3.47, 3.48]},
            {"key": "us10y", "label": "US 10-year yield", "source": "treasury", "kind": "yield", "date": "2026-10-15",
             "value": 5.10, "change": -0.12, "spark": [5.24, 5.22, 5.21, 5.19, 5.10]},
        ]}
