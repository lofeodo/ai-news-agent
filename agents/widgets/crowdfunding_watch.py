# agents/widgets/crowdfunding_watch.py
#
# On Kickstarter & Gamefound (board games): the campaigns this week's Crowdfunding articles talk about, with
# platform and status as the articles state them. Kickstarter and Gamefound have no public API, so one Haiku
# call reads the section's picks; every name must appear in its article and every link is that article's URL.

import topics

from . import derived, ui
from .base import Widget, WidgetSkip
from .registry import register

SECTION = "Crowdfunding"
PLATFORMS = ("Kickstarter", "Gamefound", "BackerKit", "Other")
STATUSES = ("live", "upcoming", "funded", "ended", "unknown")
MAX_ROWS = 6
TOOL = {
    "name": "report_campaigns",
    "description": "List the crowdfunding campaigns the articles mention.",
    "input_schema": {
        "type": "object",
        "properties": {
            "campaigns": {
                "type": "array", "maxItems": MAX_ROWS,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "platform": {"type": "string", "enum": list(PLATFORMS)},
                        "status": {"type": "string", "enum": list(STATUSES)},
                        "article_index": {"type": "integer"},
                    },
                    "required": ["name", "platform", "status", "article_index"],
                },
            },
        },
        "required": ["campaigns"],
    },
}


@register
class CrowdfundingWatch(Widget):
    id = "crowdfunding_watch"
    name = "On Kickstarter & Gamefound"
    description = "The board game crowdfunding campaigns in this week's news, live, upcoming or funded."
    topics = ("boardgames",)
    kind = "derived"

    def produce(self, ctx, config):
        articles = derived.collect(ctx.picks, sections=(SECTION,), limit=12)
        if not articles:
            raise WidgetSkip("no crowdfunding articles this week")
        try:
            newsletter = topics.get_topic(ctx.topic).name
        except topics.TopicError:
            newsletter = "this newsletter"
        prompt = (derived.read_prompt("crowdfunding_watch.txt").replace("{newsletter}", newsletter)
                  .replace("{articles}", derived.format_articles(articles)))
        answer = derived.call_tool(ctx, self.id, prompt, TOOL)
        rows, seen = [], set()
        for c in answer.get("campaigns") or []:
            if not isinstance(c, dict):
                continue
            idx, name = c.get("article_index"), derived.clean_text(c.get("name"), 80)
            if not name or not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(articles):
                continue
            art = articles[idx]
            if derived.norm(name) not in derived.norm(art["title"] + " " + art["summary"]) or name.lower() in seen:
                continue
            seen.add(name.lower())
            rows.append({"name": name, "url": art["url"],
                         "platform": c.get("platform") if c.get("platform") in PLATFORMS else "Other",
                         "status": c.get("status") if c.get("status") in STATUSES else "unknown"})
            if len(rows) >= MAX_ROWS:
                break
        if not rows:
            raise WidgetSkip("no campaign named in the articles")
        return {"rows": rows, "as_of": ctx.now.isoformat()}

    def render(self, data, config, look):
        rows = [r for r in (data or {}).get("rows") or [] if isinstance(r, dict) and r.get("name")]
        if not rows:
            return ""
        table = [[ui.link(r["name"], r.get("url")), r.get("platform") if r.get("platform") in PLATFORMS else None,
                  (r.get("status") or "").capitalize() if r.get("status") in STATUSES[:-1] else None]
                 for r in rows]
        body = ui.data_table(["Game", "Platform", "Status"], table)
        return ui.card(self.id, self.name, body, ui.accent_of(look), "🎲", None,
                       ui.attribution([], None, "Picked out by AI from this week's Crowdfunding stories; "
                                                "check the campaign page before backing."))

    def sample_data(self):
        return {"as_of": "2026-10-18T16:00:00+00:00", "rows": [
            {"name": "Example Quest: Second Edition", "platform": "Gamefound", "status": "live",
             "url": "https://example.com/example-quest"},
            {"name": "Tiny Harbour", "platform": "Kickstarter", "status": "funded",
             "url": "https://example.com/tiny-harbour"},
            {"name": "Moonlit Orchard", "platform": "Other", "status": "upcoming",
             "url": "https://example.com/moonlit-orchard"},
        ]}
