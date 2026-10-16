# agents/widgets/finance_data.py
#
# Open-licence finance sources shared by the finance widgets, all keyless (docs/widgets.md#sources):
#   Bank of Canada Valet   exchange rates, Canada bond yields, the overnight rate target (attribution required)
#   US Treasury            daily par yield curve CSV (public domain)
#   New York Fed           effective fed funds rate, which carries the Fed's target range (business use allowed
#                          with the reference-rate notice in NYFED_NOTICE)
#   ECB Data Portal        deposit facility rate (reuse with the ECB cited)
# plus the dated calendar in finance_calendar.yaml.

import csv
import io
import os
from datetime import date, datetime

import yaml

CALENDAR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "finance_calendar.yaml")
BOC_URL = "https://www.bankofcanada.ca/valet/observations/{series}/json?recent={n}"
TREASURY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
                "{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv")
TREASURY_TIMEOUT = 35   # the server takes ~17 s before its first byte, whatever the range asked for
NYFED_URL ="https://markets.newyorkfed.org/api/rates/unsecured/effr/last/{n}.json"
ECB_URL = ("https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV"
           "?lastNObservations={n}&format=jsondata")

SOURCES = {
    "boc": ("Bank of Canada", "https://www.bankofcanada.ca/rates/"),
    "treasury": ("US Treasury", "https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics"),
    "nyfed": ("New York Fed", "https://www.newyorkfed.org/markets/reference-rates/effr"),
    "ecb": ("ECB", "https://data.ecb.europa.eu/"),
}
NYFED_NOTICE = ("The Fed target range is subject to the Terms of Use at newyorkfed.org; the New York Fed does not "
                "endorse this republication")
NOT_ADVICE = "For information only, not financial advice"


class CalendarExpired(RuntimeError):
    pass


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_calendar(path: str = CALENDAR_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        cal = yaml.safe_load(f)
    events = []
    for key, ev in (cal.get("events") or {}).items():
        for d in ev.get("dates") or []:
            events.append({"key": key, "date": date.fromisoformat(str(d)), "name": ev["name"],
                           "short": ev.get("short") or ev["name"], "time_et": ev.get("time_et"),
                           "source": ev.get("source")})
    events.sort(key=lambda e: (e["date"], e["key"]))
    return {"valid_until": date.fromisoformat(str(cal["valid_until"])), "events": events}


def check_calendar(cal: dict, day: date) -> None:
    if day > cal["valid_until"]:
        raise CalendarExpired(f"finance calendar ends {cal['valid_until'].isoformat()}; add next year's dates to "
                              f"agents/widgets/finance_calendar.yaml")


def next_event(cal: dict, key: str, after: date):
    return next((e for e in cal["events"] if e["key"] == key and e["date"] >= after), None)


def boc_series(ctx, series: list[str], recent: int = 10) -> dict:
    """{series id: [(date iso, value), ...] oldest first} from one Valet call; missing series are absent."""
    payload = ctx.get_json(BOC_URL.format(series=",".join(series), n=recent))
    out: dict = {}
    for obs in reversed(payload.get("observations") or []):
        d = obs.get("d") if isinstance(obs, dict) else None
        for s in series:
            v = _float((obs.get(s) or {}).get("v")) if d else None
            if v is not None:
                out.setdefault(s, []).append((d, v))
    return out


def treasury_yields(ctx, year: int, columns=("2 Yr", "10 Yr")) -> dict:
    """{column: [(date iso, value), ...] oldest first} from the Treasury's daily par yield curve CSV."""
    text = ctx.get(TREASURY_URL.format(year=year), timeout=TREASURY_TIMEOUT).text
    out: dict = {}
    for row in reversed(list(csv.DictReader(io.StringIO(text)))):
        try:
            d = datetime.strptime(row.get("Date", ""), "%m/%d/%Y").date().isoformat()
        except ValueError:
            continue
        for c in columns:
            v = _float(row.get(c))
            if v is not None:
                out.setdefault(c, []).append((d, v))
    return out


def nyfed_target(ctx, n: int = 5) -> list[tuple]:
    """[(date iso, target from, target to), ...] oldest first."""
    rows = []
    for r in (ctx.get_json(NYFED_URL.format(n=n)) or {}).get("refRates") or []:
        lo, hi = _float((r or {}).get("targetRateFrom")), _float((r or {}).get("targetRateTo"))
        if r.get("effectiveDate") and lo is not None and hi is not None:
            rows.append((r["effectiveDate"], lo, hi))
    return sorted(rows)


def ecb_deposit_rate(ctx, n: int = 5) -> list[tuple]:
    """[(date iso, rate), ...] oldest first, from the ECB's SDMX-JSON."""
    payload = ctx.get_json(ECB_URL.format(n=n))
    dates = [v.get("id") for v in payload["structure"]["dimensions"]["observation"][0]["values"]]
    series = next(iter(payload["dataSets"][0]["series"].values()))
    rows = []
    for idx, values in series["observations"].items():
        i = int(idx)
        v = _float(values[0]) if values else None
        if 0 <= i < len(dates) and v is not None:
            rows.append((dates[i], v))
    return sorted(rows)


def change_over(points: list[tuple], days: int, today: date):
    """Latest value minus the value `days` earlier (the last point on or before then); None if unknown."""
    if not points:
        return None
    cutoff = date.fromordinal(today.toordinal() - days).isoformat()
    before = [p for p in points if p[0] <= cutoff]
    if not before:
        return None
    return points[-1][-1] - before[-1][-1]
