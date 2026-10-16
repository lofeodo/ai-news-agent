# agents/widgets/number_of_week.py
#
# Number of the week (any topic): one striking figure from this week's picks, with the sentence that explains it
# and a link to its article. One Haiku call (about $0.002). The number must appear verbatim in the article it
# names, and the link is that article's own URL, so the model can neither invent a figure nor insert a link.

import re

import topics

from . import derived, ui
from .base import Widget, WidgetSkip
from .registry import register

TOOL = {
    "name": "number_of_the_week",
    "description": "Report the week's most striking number.",
    "input_schema": {
        "type": "object",
        "properties": {
            "number": {"type": "string", "description": "the number exactly as written in the article; empty if none"},
            "context": {"type": "string", "description": "one plain sentence, at most 25 words"},
            "article_index": {"type": "integer"},
        },
        "required": ["number", "context", "article_index"],
    },
}
NUMBER_MAX, CONTEXT_MAX = 30, 220
_DIGIT = re.compile(r"\d")


@register
class NumberOfWeek(Widget):
    id = "number_of_week"
    name = "Number of the week"
    description = "One striking number from this week's stories, and what it means."
    kind = "derived"

    def produce(self, ctx, config):
        articles = derived.collect(ctx.picks)
        if not articles:
            raise WidgetSkip("no picks")
        try:
            newsletter = topics.get_topic(ctx.topic).name
        except topics.TopicError:
            newsletter = "this newsletter"
        prompt = (derived.read_prompt("number_of_week.txt").replace("{newsletter}", newsletter)
                  .replace("{articles}", derived.format_articles(articles)))
        answer = derived.call_tool(ctx, self.id, prompt, TOOL)
        number = derived.clean_text(answer.get("number"), NUMBER_MAX)
        context = derived.clean_text(answer.get("context"), CONTEXT_MAX)
        idx = answer.get("article_index")
        if not number or not _DIGIT.search(number) or not context:
            raise WidgetSkip("no number this week")
        if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(articles):
            raise ValueError("article_index out of range")
        art = articles[idx]
        if derived.norm(number) not in derived.norm(art["title"] + " " + art["summary"]):
            raise ValueError("the number does not appear in its article")
        return {"number": number, "context": context, "title": art["title"][:160], "url": art["url"],
                "as_of": ctx.now.isoformat()}

    def render(self, data, config, look):
        data = data if isinstance(data, dict) else {}
        if not data.get("number") or not data.get("context"):
            return ""
        from render.palette import ASH, CHAR, CREAM, F
        accent = ui.accent_of(look)
        body = (f'<p style="margin:0 0 6px 0;font-family:{F};font-size:34px;font-weight:700;line-height:1.1;'
                f'letter-spacing:-0.5px;color:{CREAM};">{ui.cell(data["number"])}</p>'
                f'<p style="margin:0;font-family:{F};font-size:15px;line-height:1.55;color:{ASH};">'
                f'{ui.cell(data["context"])}</p>')
        if data.get("title"):
            body += (f'<p style="margin:10px 0 0 0;font-family:{F};font-size:12px;color:{CHAR};">From: '
                     f'{ui.link(data["title"], data.get("url"), accent, underline=True)}</p>')
        return ui.card(self.id, self.name, body, accent, "#")

    def sample_data(self):
        return {"number": "$4.2 billion", "as_of": "2026-10-18T16:00:00+00:00",
                "context": "What the largest AI chip deal of the week is worth, across three years of supply.",
                "title": "Chipmaker signs multi-year supply deal", "url": "https://example.com/chip-deal"}
