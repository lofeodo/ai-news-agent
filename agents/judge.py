# agents/judge.py
#
# LLM judge for summary faithfulness: is every claim in a generated summary supported by the
# source text it was written from? Returns a binary verdict (matching the `supported` column of
# evals/labels/summaries_template.csv) plus the unsupported claims for audit. Pure apart from the
# one Claude call, so it is stub-tested; the weekly runner is agents/online_judge.py and the
# calibration against human labels is evals/run_judge_calibration.py.

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import JUDGE_MODEL, JUDGE_MAX_TOKENS, JUDGE_MAX_SOURCE_WORDS
from prompt_guard import GUARD_XML_TAGS, neutralize_tags

_PROMPT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts", "judge_prompt.txt")

VERDICT_TOOL = {
    "name": "record_verdict",
    "description": "Record whether the summary is fully supported by the source text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "supported": {"type": "boolean", "description": "True if every factual claim is supported and no significance sentence adds an unsupported specific."},
            "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["supported", "unsupported_claims"],
    },
}


def load_prompt() -> str:
    with open(_PROMPT_PATH, encoding="utf-8") as f:
        return f.read()


def truncate_words(text: str, limit: int = JUDGE_MAX_SOURCE_WORDS) -> str:
    words = (text or "").split()
    return " ".join(words[:limit])


def build_prompt(template: str, title: str, source: str, summary: str) -> str:
    return template.format(
        title=neutralize_tags(title),
        source=neutralize_tags(truncate_words(source)),
        summary=neutralize_tags(summary),
    )


def judge_summary(client, title: str, source: str, summary: str, template: str | None = None, model: str = JUDGE_MODEL) -> dict:
    """One judge call. Returns {supported, unsupported_claims, input_tokens, output_tokens}.

    Raises ValueError if the model does not return the verdict tool call, so a caller can count
    the item as errored rather than guess a label.
    """
    prompt = build_prompt(template or load_prompt(), title, source, summary)
    response = client.messages.create(
        model=model,
        max_tokens=JUDGE_MAX_TOKENS,
        system=GUARD_XML_TAGS,
        tools=[VERDICT_TOOL],
        tool_choice={"type": "tool", "name": VERDICT_TOOL["name"]},
        messages=[{"role": "user", "content": prompt}],
    )
    usage = getattr(response, "usage", None)
    for block in response.content or []:
        if getattr(block, "type", None) == "tool_use" and block.name == VERDICT_TOOL["name"]:
            data = block.input or {}
            if not isinstance(data.get("supported"), bool):
                break
            claims = [str(c) for c in (data.get("unsupported_claims") or [])]
            return {
                "supported": data["supported"],
                "unsupported_claims": claims,
                "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            }
    raise ValueError("judge returned no valid record_verdict tool call")
