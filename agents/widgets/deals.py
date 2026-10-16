# agents/widgets/deals.py
#
# Best deals (video games): the best-rated PC game discounts from the CheapShark API (free, no key). CheapShark's
# one condition is that deal links go through CheapShark, so every link is its /redirect?dealID= link.

from urllib.parse import quote, unquote

from . import ui
from .base import Widget, WidgetSkip
from .registry import register

DEALS_URL = "https://www.cheapshark.com/api/1.0/deals?pageSize=15&sortBy=Deal%20Rating&onSale=1"
STORES_URL = "https://www.cheapshark.com/api/1.0/stores"
REDIRECT = "https://www.cheapshark.com/redirect?dealID={deal}"
TOP_N = 5


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@register
class Deals(Widget):
    id = "deals"
    name = "Best deals"
    description = "The week's best-rated PC game discounts across the big stores."
    topics = ("gaming",)

    def produce(self, ctx, config):
        deals = ctx.get_json(DEALS_URL)
        if not isinstance(deals, list):
            raise ValueError("CheapShark deals is not a list")
        try:
            stores = {str(s.get("storeID")): s.get("storeName") for s in ctx.get_json(STORES_URL)
                      if isinstance(s, dict) and s.get("isActive")}
        except Exception:
            stores = {}
        rows, seen = [], set()
        for d in deals:
            if not isinstance(d, dict) or not isinstance(d.get("title"), str) or not d.get("dealID"):
                continue
            title = d["title"].strip()[:80]
            sale, normal, savings = _num(d.get("salePrice")), _num(d.get("normalPrice")), _num(d.get("savings"))
            if not title or title.lower() in seen or sale is None or normal is None or sale >= normal:
                continue
            if stores and str(d.get("storeID")) not in stores:
                continue
            seen.add(title.lower())
            rows.append({"title": title, "store": stores.get(str(d.get("storeID"))) or "",
                         "sale": round(sale, 2), "normal": round(normal, 2),
                         "savings": round(savings) if savings is not None else None,
                         "deal": quote(unquote(str(d["dealID"])), safe="")})
            if len(rows) >= TOP_N:
                break
        if not rows:
            raise WidgetSkip("no deals")
        return {"rows": rows, "as_of": ctx.now.isoformat(), "currency": "USD"}

    def render(self, data, config, look):
        rows = [r for r in (data or {}).get("rows") or [] if isinstance(r, dict) and r.get("title")]
        if not rows:
            return ""
        table = []
        for r in rows:
            sale, normal = _num(r.get("sale")), _num(r.get("normal"))
            price = ui.Raw(f"${sale:,.2f} " + ui.muted(f"${normal:,.2f}")) if sale is not None and normal else None
            title = ui.link(r["title"], REDIRECT.format(deal=quote(str(r.get("deal") or ""), safe="%")))
            table.append([ui.Raw(title + (ui.Raw("<br>") + ui.muted(r["store"]) if r.get("store") else "")),
                          price, f"−{r['savings']}%" if isinstance(r.get("savings"), int) else None])
        body = ui.data_table(["Game", "Price (USD)", "Off"], table, numeric=(1, 2))
        return ui.card(self.id, self.name, body, ui.accent_of(look), "🏷", None,
                       ui.attribution([("CheapShark", "https://www.cheapshark.com")], data.get("as_of"),
                                      "Prices change fast; check the store."))

    def sample_data(self):
        return {"as_of": "2026-10-18T16:00:00+00:00", "currency": "USD", "rows": [
            {"title": "The Witcher 3: Wild Hunt Remastered", "store": "GOG", "sale": 24.99, "normal": 49.99,
             "savings": 50, "deal": "OR%2BVz0QIAULaNU5tjUdvoTs3iqGm6lodrS%2BXzXvxPXg%3D"},
            {"title": "Silver Pines", "store": "Steam", "sale": 22.26, "normal": 35.98, "savings": 38,
             "deal": "HNPl2c6%2BV040vKq07rLwKPiQSYrZsTs4Zo%2FSqEhREYE%3D"},
        ]}
