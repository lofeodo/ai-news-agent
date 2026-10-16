# agents/widgets/rates_board.py
#
# Central bank rates (finance): the Bank of Canada's overnight target, the Fed's target range and the ECB's deposit
# facility rate, each with its change over the past week and the date of the next decision. All keyless
# (finance_data.py); a bank whose source fails is one missing row, not a missing widget.

from datetime import date, timedelta

from . import finance_data as fd
from . import ui
from .base import Widget
from .registry import register


def _send_day(ctx) -> date:
    try:
        return date.fromisoformat(ctx.week)
    except (TypeError, ValueError):
        return ctx.now.date()


@register
class RatesBoard(Widget):
    id = "rates_board"
    name = "Central bank rates"
    description = "Where the Bank of Canada, the Fed and the ECB have their policy rates, and when they decide next."
    topics = ("finance",)
    max_age_hours = 7 * 24

    def produce(self, ctx, config):
        today = _send_day(ctx)
        cal = fd.load_calendar()
        fd.check_calendar(cal, today)

        def nxt(key):
            e = fd.next_event(cal, key, today)
            return e["date"].isoformat() if e else None

        rows, errors = [], []
        fetchers = (
            ("boc", lambda: fd.boc_series(ctx, ["V39079"], 10).get("V39079") or []),
            ("fomc", lambda: fd.nyfed_target(ctx, 10)),
            ("ecb", lambda: fd.ecb_deposit_rate(ctx, 10)),
        )
        labels = {"boc": ("Bank of Canada", "Overnight rate target"), "fomc": ("Federal Reserve", "Target range"),
                  "ecb": ("European Central Bank", "Deposit facility rate")}
        for key, fetch in fetchers:
            try:
                points = fetch()
            except Exception as e:
                errors.append(f"{key}: {type(e).__name__}")
                continue
            if not points:
                errors.append(f"{key}: no data")
                continue
            last = points[-1]
            change = fd.change_over(points, 7, today - timedelta(days=1))
            bank, label = labels[key]
            rows.append({"key": key, "bank": bank, "label": label, "date": last[0],
                         "rate": last[1], "rate_hi": last[2] if len(last) > 2 else None,
                         "change_bp": round(change * 100) if change is not None else None, "next": nxt(key)})
        if not rows:
            raise RuntimeError("no central bank rate could be fetched (" + "; ".join(errors) + ")")
        return {"rows": rows, "as_of": max(r["date"] for r in rows)}

    def render(self, data, config, look):
        rows = [r for r in (data or {}).get("rows") or [] if isinstance(r, dict) and r.get("bank")]
        if not rows:
            return ""
        table = []
        for r in rows:
            lo, hi = r.get("rate"), r.get("rate_hi")
            if not isinstance(lo, (int, float)):
                continue
            rate = f"{lo:.2f}–{hi:.2f}%" if isinstance(hi, (int, float)) else f"{lo:.2f}%"
            change = r.get("change_bp")
            rate_cell = ui.Raw(ui.cell(rate) + (" " + ui.delta(change, 0, " bp") if change else ""))
            table.append([ui.Raw(ui.cell(r["bank"]) + "<br>" + ui.muted(r.get("label") or "")), rate_cell,
                          ui.fmt_date(r.get("next"), with_year=False) or None])
        if not table:
            return ""
        body = ui.data_table(["Central bank", "Rate", "Next decision"], table, numeric=(1, 2))
        used = {r.get("key") for r in rows}
        sources = [fd.SOURCES[k] for k, used_by in (("boc", "boc"), ("nyfed", "fomc"), ("ecb", "ecb")) if used_by in used]
        note = fd.NOT_ADVICE + ". Weekly change computed by us"
        if "fomc" in used:
            note += ". " + fd.NYFED_NOTICE
        return ui.card(self.id, self.name, body, ui.accent_of(look), "🏦", None,
                       ui.attribution(sources, data.get("as_of"), note + "."))

    def sample_data(self):
        return {"as_of": "2026-10-15", "rows": [
            {"key": "boc", "bank": "Bank of Canada", "label": "Overnight rate target", "date": "2026-10-15",
             "rate": 2.25, "rate_hi": None, "change_bp": None, "next": "2026-10-28"},
            {"key": "fomc", "bank": "Federal Reserve", "label": "Target range", "date": "2026-10-15",
             "rate": 3.75, "rate_hi": 4.0, "change_bp": -25, "next": "2026-10-28"},
            {"key": "ecb", "bank": "European Central Bank", "label": "Deposit facility rate", "date": "2026-10-16",
             "rate": 2.5, "rate_hi": None, "change_bp": None, "next": "2026-10-29"},
        ]}
