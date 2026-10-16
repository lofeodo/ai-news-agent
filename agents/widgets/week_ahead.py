# agents/widgets/week_ahead.py
#
# The week ahead (finance): central bank decisions and major US releases in the send week, from the dated
# calendar in finance_calendar.yaml (no network). A quiet week shows what comes next instead. When the send date
# passes the calendar's valid_until, the widget fails with a message telling the owner to add next year's dates.

from datetime import date, timedelta

from . import finance_data as fd
from . import ui
from .base import Widget
from .registry import register

COMING_UP = 3
CALENDARS = [("Fed", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"),
             ("Bank of Canada", "https://www.bankofcanada.ca/press/upcoming-events/"),
             ("ECB", "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"),
             ("US BLS", "https://www.bls.gov/schedule/")]


@register
class WeekAhead(Widget):
    id = "week_ahead"
    name = "The week ahead"
    description = "Central bank decisions and major economic releases coming up this week."
    topics = ("finance",)

    def produce(self, ctx, config):
        try:
            start = date.fromisoformat(ctx.week)
        except (TypeError, ValueError):
            start = ctx.now.date()
        cal = fd.load_calendar()
        fd.check_calendar(cal, start)
        end = start + timedelta(days=7)
        events = [e for e in cal["events"] if start <= e["date"] < end]
        quiet = not events
        if quiet:
            events = [e for e in cal["events"] if e["date"] >= end][:COMING_UP]
        return {"week_start": start.isoformat(), "quiet": quiet,
                "events": [{"date": e["date"].isoformat(), "name": e["name"], "time_et": e["time_et"],
                            "source": e["source"]} for e in events]}

    def render(self, data, config, look):
        data = data if isinstance(data, dict) else {}
        items = []
        for e in data.get("events") or []:
            if not isinstance(e, dict) or not e.get("name"):
                continue
            try:
                d = date.fromisoformat(str(e.get("date")))
            except ValueError:
                continue
            when = f"{d.strftime('%a')} {ui.fmt_date(d)}" + (f" · {e['time_et']} ET" if e.get("time_et") else "")
            items.append((ui.link(e["name"], e.get("source")) if e.get("source") else e["name"], when))
        if not items:
            return ""
        aside = "quiet week · coming up" if data.get("quiet") else None
        return ui.card(self.id, self.name, ui.item_list(items), ui.accent_of(look), "📅", aside,
                       ui.attribution(CALENDARS, None, "official calendars. " + fd.NOT_ADVICE + "."))

    def sample_data(self):
        return {"week_start": "2026-10-26", "quiet": False, "events": [
            {"date": "2026-10-28", "name": "Bank of Canada rate decision", "time_et": "9:45 AM",
             "source": "https://www.bankofcanada.ca/"},
            {"date": "2026-10-28", "name": "Federal Reserve (FOMC) rate decision", "time_et": None,
             "source": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"},
            {"date": "2026-10-29", "name": "European Central Bank rate decision", "time_et": None,
             "source": "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"},
        ]}
