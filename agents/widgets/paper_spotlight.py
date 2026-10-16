# agents/widgets/paper_spotlight.py
#
# Paper of the week (AI): a pipeline widget. agent1a picks the paper and agent2a summarizes it; this widget only
# reads the run doc's paper_summaries and draws today's dark hero card, byte for byte.

from render.blocks import research_rows

from .base import Widget, WidgetSkip
from .registry import register

PAPER_FIELDS = ("title", "authors", "summary", "hf_url", "pdf_url")


def slim(paper: dict) -> dict:
    """Only what the card draws (the scores' reasoning text would push the data over its size cap)."""
    out = {k: paper.get(k) for k in PAPER_FIELDS if paper.get(k) is not None}
    upvotes = (paper.get("scores") or {}).get("upvotes")
    if upvotes is not None:
        out["scores"] = {"upvotes": upvotes}
    return out


@register
class PaperSpotlight(Widget):
    id = "paper_spotlight"
    name = "Paper of the week"
    description = "The week's most interesting AI research paper, explained in two sentences."
    topics = ("ai",)
    kind = "pipeline"
    toc = ("RES", "RESEARCH")

    def produce(self, ctx, config):
        papers = [slim(p) for p in (ctx.run_doc.get("paper_summaries") or [])
                  if (p.get("summary") or "").strip()]
        if not papers:
            raise WidgetSkip("no summarized paper")
        return {"papers": papers}

    def render(self, data, config, look):
        papers = [p for p in (data or {}).get("papers") or [] if isinstance(p, dict)]
        return research_rows(papers) if papers else ""

    def sample_data(self):
        return {"papers": [{
            "title": "Small Models Learn to Check Their Own Arithmetic",
            "authors": ["A. Researcher", "B. Scientist", "C. Engineer", "D. Student"],
            "summary": "A 1B-parameter model learns to spot and fix its own arithmetic slips by re-deriving each "
                       "step backwards. It matters because cheap models could become reliable calculators.",
            "hf_url": "https://huggingface.co/papers/2610.00001",
            "scores": {"upvotes": 128},
        }]}
