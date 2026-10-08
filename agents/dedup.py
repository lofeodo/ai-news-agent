# agents/dedup.py
#
# Duplicate detector for agent3's article selection. One DedupConversation per
# (section, selection pass): start() sends every selected article once, add()
# then checks a single new article against the earlier ones without resending
# them. It only reports duplicate groups; which member to keep is the caller's
# policy. It raises DedupError on any failure and never swallows it, so the
# caller can degrade to the undeduped selection.

from dataclasses import dataclass
from pathlib import Path

from config import DEDUP_MODEL, DEDUP_MAX_TOKENS
from dedup_tool import REPORT_DUPLICATES_TOOL
from prompt_guard import GUARD_DEDUP, neutralize_tags

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
_SUMMARY_CHARS = 300


class DedupError(Exception):
    """The duplicate check could not be completed."""


@dataclass(frozen=True)
class DuplicateGroup:
    indices: tuple
    reason: str = ""


def _load(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8")


def format_article(index: int, article: dict) -> str:
    """Same shape as agent3's selection listing, minus the HN label (irrelevant here)."""
    return (
        f"<article_{index}>\n"
        f"[{index}] {neutralize_tags(article.get('title', 'No title'))}\n"
        f"    Summary: {neutralize_tags((article.get('summary') or article.get('description') or '')[:_SUMMARY_CHARS])}\n"
        f"</article_{index}>"
    )


def clean_groups(raw, valid: set) -> list:
    """Validate the model's groups: keep ints in `valid`, dedupe, drop singletons, merge overlaps."""
    merged = []   # list of [set_of_indices, reasons]
    for g in raw if isinstance(raw, list) else []:
        if not isinstance(g, dict) or not isinstance(g.get("indices"), list):
            continue
        idx = {i for i in g["indices"] if isinstance(i, int) and not isinstance(i, bool) and i in valid}
        if len(idx) < 2:
            continue
        reason = g.get("reason") if isinstance(g.get("reason"), str) else ""
        hits = [m for m in merged if m[0] & idx]
        for m in hits:
            idx |= m[0]
            reason = m[1] or reason
            merged.remove(m)
        merged.append([idx, reason])
    return sorted((DuplicateGroup(tuple(sorted(i)), r) for i, r in merged), key=lambda g: g.indices)


class DedupConversation:
    def __init__(self, client, category: str = "", model: str = DEDUP_MODEL, create=None):
        self._category = category
        self._create = create or client.messages.create
        self.model = model
        self.messages = []
        self.count = 0                       # articles numbered so far
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    # -- public ------------------------------------------------------------
    def start(self, articles: list) -> list:
        """Send every selected article; returns the duplicate groups (indices 0..n-1)."""
        if self.messages:
            raise DedupError("conversation already started")
        if not articles:
            return []
        listing = "\n\n".join(format_article(i, a) for i, a in enumerate(articles))
        prompt = _load("dedup_prompt.txt").format(category=self._category, articles=listing)
        self.count = len(articles)
        return clean_groups(self._turn({"role": "user", "content": prompt}), set(range(self.count)))

    def add(self, article: dict):
        """Check one new article against all earlier ones; returns its DuplicateGroup or None."""
        if not self.messages:
            raise DedupError("start() must be called before add()")
        number = self.count
        self.count += 1
        text = _load("dedup_candidate_prompt.txt").format(number=number, article=format_article(number, article))
        # the tool_result for the previous tool_use must come first in this user turn
        content = [{"type": "tool_result", "tool_use_id": self._last_tool_id, "content": "recorded"},
                   {"type": "text", "text": text}]
        groups = clean_groups(self._turn({"role": "user", "content": content}), set(range(self.count)))
        return next((g for g in groups if number in g.indices), None)

    # -- internals -----------------------------------------------------------
    def _turn(self, user_msg: dict):
        pending = self.messages + [user_msg]
        for attempt in range(2):             # tool_choice is auto, so a missing call gets one retry
            try:
                resp = self._create(
                    model=self.model, max_tokens=DEDUP_MAX_TOKENS, system=GUARD_DEDUP,
                    tools=[REPORT_DUPLICATES_TOOL], tool_choice={"type": "auto"}, messages=pending,
                )
            except Exception as e:
                raise DedupError(f"dedup call failed: {e}") from e
            usage = getattr(resp, "usage", None)
            self.usage["input_tokens"] += getattr(usage, "input_tokens", 0) or 0
            self.usage["output_tokens"] += getattr(usage, "output_tokens", 0) or 0
            call = next((b for b in (resp.content or []) if b.type == "tool_use" and b.name == "report_duplicates"), None)
            if call is not None:
                self.messages = pending + [{"role": "assistant", "content": [
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}]}]
                self._last_tool_id = call.id
                return (call.input or {}).get("groups")
        raise DedupError("model did not call report_duplicates")
