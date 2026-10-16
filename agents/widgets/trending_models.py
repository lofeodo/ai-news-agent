# agents/widgets/trending_models.py
#
# Trending on Hugging Face (AI): the five models trending on the Hugging Face Hub right now, with their task and
# likes. Public API, no key. Not placed in the AI newsletter (its readers see no change); custom newsletters may
# add it.

import re

from . import ui
from .base import Widget, WidgetSkip
from .registry import register

API_URL = "https://huggingface.co/api/models?sort=trendingScore&limit=8"
MODEL_URL = "https://huggingface.co/{id}"
TOP_N = 5
_ID_RE = re.compile(r"^[A-Za-z0-9][\w.\-]{0,95}/[\w.\-]{1,96}$")


def task_label(tag) -> str:
    """'text-generation' -> 'Text generation'."""
    return str(tag).replace("-", " ").capitalize()[:40] if isinstance(tag, str) and tag else ""


@register
class TrendingModels(Widget):
    id = "trending_models"
    name = "Trending on Hugging Face"
    description = "The five AI models trending on Hugging Face this week, with what they do."
    topics = ("ai",)

    def produce(self, ctx, config):
        models = ctx.get_json(API_URL)
        if not isinstance(models, list):
            raise ValueError("Hugging Face answer is not a list")
        rows = []
        for m in models:
            mid = (m or {}).get("id") if isinstance(m, dict) else None
            if not isinstance(mid, str) or not _ID_RE.match(mid) or m.get("private"):
                continue
            likes = m.get("likes")
            rows.append({"id": mid, "task": task_label(m.get("pipeline_tag")),
                         "likes": likes if isinstance(likes, int) and not isinstance(likes, bool) else None})
            if len(rows) >= TOP_N:
                break
        if not rows:
            raise WidgetSkip("no trending models")
        return {"rows": rows, "as_of": ctx.now.isoformat()}

    def render(self, data, config, look):
        rows = [r for r in (data or {}).get("rows") or [] if isinstance(r, dict) and isinstance(r.get("id"), str)]
        if not rows:
            return ""
        table = [[ui.link(r["id"], MODEL_URL.format(id=r["id"]) if _ID_RE.match(r["id"]) else None),
                  r.get("task") or None, f"{r['likes']:,}" if isinstance(r.get("likes"), int) else None]
                 for r in rows]
        body = ui.data_table(["Model", "Task", "Likes"], table, numeric=(2,))
        return ui.card(self.id, self.name, body, ui.accent_of(look), "🤗", None,
                       ui.attribution([("Hugging Face", "https://huggingface.co/models?sort=trending")],
                                      data.get("as_of")))

    def sample_data(self):
        return {"as_of": "2026-10-18T16:00:00+00:00", "rows": [
            {"id": "google/embeddinggemma-2", "task": "Feature extraction", "likes": 2494},
            {"id": "openbmb/MiniCPM-V-4.7-35B-A3B", "task": "Image text to text", "likes": 831},
            {"id": "example-lab/tiny-coder-1b", "task": "Text generation", "likes": 402},
        ]}
