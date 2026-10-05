# agents/agent1b_graph.py
#
# LangGraph implementation of agent1b's collect -> filter -> categorize stage.
# Lives *inside* the agent: Pub/Sub + the Firestore counter still coordinate
# between agents. Local and cloud mode run this same code path.
#
#   fetch -> prefilter -> language_filter -> categorize --route--> finalize
#                                                          \-> review (per low-confidence article, parallel)
#   review subgraph: llm_call --(fetch_article_text)--> tool_exec --> llm_call
#                              \--(submit_category / cap / error)--> finish
#
# The Anthropic SDK is used directly (no langchain-anthropic, no prebuilt ReAct
# agent) so the tool loop is visible as graph nodes.

import concurrent.futures
import json
import operator
import os
import re
import threading
from dataclasses import dataclass
from typing import Annotated, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

import agent1b_fetch_news as a1b
import tracing
from prompt_guard import GUARD_REVIEW, neutralize_tags
from config import SCORING_MODEL
from filter_tool import CATEGORIES, FETCH_ARTICLE_TOOL, SUBMIT_CATEGORY_TOOL

REVIEW_MAX_TOKENS = 500


# ---------------------------------------------------------------------------
# Config (env vars, safe defaults)
# ---------------------------------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ReviewConfig:
    confidence_threshold: int = 4    # review when confidence < this (or missing)
    max_articles: int = 30           # per-run cap on reviewed articles; 0 disables review
    max_iterations: int = 3          # LLM calls per reviewed article
    fetch_timeout: int = 10          # seconds per article fetch

    @classmethod
    def from_env(cls) -> "ReviewConfig":
        return cls(
            confidence_threshold=_env_int("REVIEW_CONFIDENCE_THRESHOLD", cls.confidence_threshold),
            max_articles=max(0, _env_int("REVIEW_MAX_ARTICLES", cls.max_articles)),
            max_iterations=max(1, _env_int("REVIEW_MAX_ITERATIONS", cls.max_iterations)),
            fetch_timeout=max(1, _env_int("REVIEW_FETCH_TIMEOUT", cls.fetch_timeout)),
        )


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def merge_counts(a: dict, b: dict) -> dict:
    """Reducer: recursively add numeric leaves of two dicts."""
    out = dict(a or {})
    for k, v in (b or {}).items():
        if isinstance(v, dict):
            out[k] = merge_counts(out.get(k, {}), v)
        else:
            out[k] = out.get(k, 0) + v
    return out


class AgentState(TypedDict, total=False):
    run_id: str
    articles: list                                   # after fetch / prefilter / language filter
    categorized: list                                # first pass: article + category + confidence
    to_review: list                                  # low-confidence articles selected (capped)
    capped_urls: list                                # low-confidence but over the per-run cap
    reviewed: Annotated[list, operator.add]          # one result per reviewed article (parallel merge)
    tool_call_counts: Annotated[dict, merge_counts]
    token_usage: Annotated[dict, merge_counts]       # {node: {input, output, calls}}
    final: list                                      # exactly what single-pass filter_and_categorize returns
    audit: list                                      # per-article eval log (additive, separate file)


class ReviewState(TypedDict, total=False):
    article: dict
    messages: list
    iterations: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    decision: dict | None
    error: str | None
    # keys shared with the parent graph (merged through its reducers)
    reviewed: Annotated[list, operator.add]
    tool_call_counts: Annotated[dict, merge_counts]
    token_usage: Annotated[dict, merge_counts]


# ---------------------------------------------------------------------------
# Token metering
# ---------------------------------------------------------------------------

class _Meter:
    def __init__(self):
        self._lock = threading.Lock()
        self.input = self.output = self.calls = 0

    def record(self, response) -> None:
        usage = getattr(response, "usage", None)
        with self._lock:
            self.calls += 1
            self.input += getattr(usage, "input_tokens", 0) or 0
            self.output += getattr(usage, "output_tokens", 0) or 0

    def as_usage(self) -> dict:
        return {"input": self.input, "output": self.output, "calls": self.calls}


class _MeteredMessages:
    def __init__(self, inner, meter):
        self._inner, self._meter = inner, meter

    def create(self, **kwargs):
        response = self._inner.create(**kwargs)
        self._meter.record(response)
        return response


class MeteredClient:
    """Thin proxy so existing helpers (claude_call_with_retry) keep working while tokens are counted."""

    def __init__(self, client, meter: _Meter):
        self.messages = _MeteredMessages(client.messages, meter)


# ---------------------------------------------------------------------------
# Review helpers
# ---------------------------------------------------------------------------

def _category_definitions() -> str:
    """Reuse the category definitions from the filter prompt so the two can't drift."""
    try:
        with open("prompts/news_filter_prompt.txt", "r", encoding="utf-8") as f:
            m = re.search(r"Categories:\n(.*?)\n\nSelection criteria:", f.read(), re.S)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "\n".join(f"- {c}" for c in CATEGORIES)


def _block_to_param(block) -> dict:
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return {"type": "text", "text": getattr(block, "text", "")}


def _fetch_with_timeout(fetcher: Callable, url: str, timeout: int) -> tuple:
    """Run the fetcher with a wall-clock cap (newspaper's parse() itself has no timeout)."""
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(fetcher, url, timeout).result(timeout=timeout + 5)
    except concurrent.futures.TimeoutError:
        return None, "timeout"
    except Exception as e:
        return None, f"fetch_error: {e}"
    finally:
        pool.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def build_review_graph(client, fetcher: Callable, cfg: ReviewConfig):
    """ReAct loop as two nodes (llm_call, tool_exec) with a conditional edge back."""
    prompt_template = None
    categories = None

    def _prompt(article: dict, first_category: str, confidence) -> str:
        nonlocal prompt_template, categories
        if prompt_template is None:
            with open("prompts/news_review_prompt.txt", "r", encoding="utf-8") as f:
                prompt_template = f.read()
            categories = _category_definitions()
        return prompt_template.format(
            categories=categories,
            title=neutralize_tags(article.get("title", "") or ""),
            description=neutralize_tags(article.get("description", "") or "(none)"),
            url=neutralize_tags(article.get("url", "")),
            first_category=first_category,
            confidence=confidence if confidence is not None else "unknown",
        )

    def llm_call(state: ReviewState) -> dict:
        article    = state["article"]
        iterations = state.get("iterations", 0)
        messages   = state.get("messages") or [
            {"role": "user", "content": _prompt(article, article["category"], article.get("confidence"))}
        ]
        # Last allowed turn: force the decision instead of another fetch.
        if iterations + 1 >= cfg.max_iterations:
            tool_choice = {"type": "tool", "name": "submit_category"}
        else:
            tool_choice = {"type": "any"}

        meter = _Meter()
        try:
            with a1b._semaphore:
                response = a1b.claude_call_with_retry(
                    MeteredClient(client, meter),
                    model=SCORING_MODEL,
                    max_tokens=REVIEW_MAX_TOKENS,
                    system=GUARD_REVIEW,
                    tools=[FETCH_ARTICLE_TOOL, SUBMIT_CATEGORY_TOOL],
                    tool_choice=tool_choice,
                    messages=messages,
                )
        except Exception as e:
            return {"iterations": iterations + 1, "error": f"llm_error: {e}",
                    "input_tokens": state.get("input_tokens", 0) + meter.input,
                    "output_tokens": state.get("output_tokens", 0) + meter.output,
                    "token_usage": {"review": meter.as_usage()}}

        update = {
            "iterations": iterations + 1,
            "input_tokens": state.get("input_tokens", 0) + meter.input,
            "output_tokens": state.get("output_tokens", 0) + meter.output,
            "token_usage": {"review": meter.as_usage()},
            "messages": messages + [{"role": "assistant", "content": [_block_to_param(b) for b in response.content]}],
        }
        submit = next((b for b in response.content if b.type == "tool_use" and b.name == "submit_category"), None)
        if submit is not None:
            category = (submit.input or {}).get("category")
            if category in CATEGORIES:
                update["decision"] = {"category": category, "reason": (submit.input or {}).get("reason", "")}
            else:
                update["error"] = f"invalid_category: {category!r}"
        elif not any(b.type == "tool_use" for b in response.content):
            update["error"] = "no_tool_call"
        return update

    def after_llm(state: ReviewState) -> str:
        if state.get("decision") or state.get("error"):
            return "finish"
        if state.get("iterations", 0) >= cfg.max_iterations:
            return "finish"      # cap hit without a decision -> finish() degrades
        return "tool_exec"

    def tool_exec(state: ReviewState) -> dict:
        last = state["messages"][-1]["content"]
        results, fetches = [], 0
        for block in last:
            if block["type"] != "tool_use":
                continue
            if block["name"] != "fetch_article_text":
                results.append({"type": "tool_result", "tool_use_id": block["id"],
                                "content": f"Unknown tool {block['name']}", "is_error": True})
                continue
            fetches += 1
            url = (block["input"] or {}).get("url") or state["article"].get("url", "")
            text, reason = _fetch_with_timeout(fetcher, url, cfg.fetch_timeout)
            if not text:
                # Failure policy: never fail the run; the review degrades to first-pass.
                return {"tool_calls": state.get("tool_calls", 0) + fetches,
                        "tool_call_counts": {"fetch_article_text": fetches},
                        "error": f"fetch_failed: {reason}"}
            results.append({"type": "tool_result", "tool_use_id": block["id"],
                            "content": f"<article_text>\n{neutralize_tags(text)}\n</article_text>"})
        return {"tool_calls": state.get("tool_calls", 0) + fetches,
                "tool_call_counts": {"fetch_article_text": fetches},
                "messages": state["messages"] + [{"role": "user", "content": results}]}

    def finish(state: ReviewState) -> dict:
        article  = state["article"]
        decision = state.get("decision")
        if decision:
            status, category, reason = "reviewed", decision["category"], decision.get("reason", "")
        else:
            status   = "review_failed"
            category = article["category"]
            reason   = state.get("error") or "iteration_cap"
        return {"reviewed": [{
            "url": article["url"], "final_category": category, "status": status, "reason": reason,
            "tool_calls": state.get("tool_calls", 0),
            "input_tokens": state.get("input_tokens", 0),
            "output_tokens": state.get("output_tokens", 0),
        }]}

    g = StateGraph(ReviewState)
    g.add_node("llm_call", llm_call)
    g.add_node("tool_exec", tool_exec)
    g.add_node("finish", finish)
    g.add_edge(START, "llm_call")
    g.add_conditional_edges("llm_call", after_llm, {"tool_exec": "tool_exec", "finish": "finish"})
    g.add_conditional_edges("tool_exec", lambda s: "finish" if s.get("error") else "llm_call",
                            {"llm_call": "llm_call", "finish": "finish"})
    g.add_edge("finish", END)
    return g.compile()


def build_graph(client=None, fetcher: Callable | None = None, cfg: ReviewConfig | None = None,
                run_id: str | None = None):
    """Compile the agent1b graph. client/fetcher are injectable for tests."""
    cfg     = cfg or ReviewConfig.from_env()
    client  = client or tracing.make_client("agent1b", run_id)
    if fetcher is None:
        from article_fetch import fetch_article_text_result
        fetcher = fetch_article_text_result

    def _metered(node: str):
        meter = _Meter()
        return meter, MeteredClient(client, meter)

    def fetch(state: AgentState) -> dict:
        articles = a1b.fetch_hn_articles() + a1b.fetch_newsapi_articles()
        print(f"\nMerged: {len(articles)} articles total")
        return {"articles": articles}

    def prefilter(state: AgentState) -> dict:
        print("Pre-filtering...")
        return {"articles": a1b.prefilter(state["articles"])}

    def language_filter(state: AgentState) -> dict:
        meter, mclient = _metered("language_filter")
        articles = a1b.language_filter(state["articles"], client=mclient)
        return {"articles": articles, "token_usage": {"language_filter": meter.as_usage()}}

    def categorize(state: AgentState) -> dict:
        meter, mclient = _metered("categorize")
        categorized = a1b.filter_and_categorize(state["articles"], client=mclient, with_confidence=True)

        low = [a for a in categorized
               if a.get("confidence") is None or a["confidence"] < cfg.confidence_threshold]
        low.sort(key=lambda a: (a["confidence"] is not None, a["confidence"] or 0))  # least sure first
        to_review, capped = low[:cfg.max_articles], low[cfg.max_articles:]
        print(f"  Confidence routing: {len(categorized) - len(low)} confident, "
              f"{len(to_review)} to review, {len(capped)} over cap")
        return {"categorized": categorized, "to_review": to_review,
                "capped_urls": [a["url"] for a in capped],
                "token_usage": {"categorize": meter.as_usage()}}

    def route(state: AgentState):
        if not state.get("to_review"):
            return "finalize"
        return [Send("review", {"article": a, "messages": [], "iterations": 0}) for a in state["to_review"]]

    def finalize(state: AgentState) -> dict:
        reviewed = {r["url"]: r for r in state.get("reviewed", [])}
        capped   = set(state.get("capped_urls", []))
        final, audit = [], []
        for a in state.get("categorized", []):
            first = a["category"]
            r     = reviewed.get(a["url"])
            cat   = r["final_category"] if r else first
            out   = {k: v for k, v in a.items() if k != "confidence"}
            out["category"] = cat
            final.append(out)
            audit.append({
                "url": a["url"],
                "first_pass_category": first,
                "confidence": a.get("confidence"),
                "routed_to_review": r is not None,
                "review_status": r["status"] if r else ("capped" if a["url"] in capped else "skipped"),
                "review_reason": r["reason"] if r else None,
                "final_category": cat,
                "tool_calls": r["tool_calls"] if r else 0,
                "input_tokens": r["input_tokens"] if r else 0,
                "output_tokens": r["output_tokens"] if r else 0,
            })
        return {"final": final, "audit": audit}

    g = StateGraph(AgentState)
    g.add_node("fetch", fetch)
    g.add_node("prefilter", prefilter)
    g.add_node("language_filter", language_filter)
    g.add_node("categorize", categorize)
    g.add_node("review", build_review_graph(client, fetcher, cfg))
    g.add_node("finalize", finalize)
    g.add_edge(START, "fetch")
    g.add_edge("fetch", "prefilter")
    g.add_edge("prefilter", "language_filter")
    g.add_edge("language_filter", "categorize")
    g.add_conditional_edges("categorize", route, ["review", "finalize"])
    g.add_edge("review", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def run_graph(run_id: str, client=None, fetcher: Callable | None = None, cfg: ReviewConfig | None = None) -> dict:
    """Run the graph; returns the final state (articles, final, audit, token_usage, ...)."""
    tracing.configure()
    graph = build_graph(client=client, fetcher=fetcher, cfg=cfg, run_id=run_id)
    return graph.invoke(
        {"run_id": run_id, "reviewed": [], "tool_call_counts": {}, "token_usage": {}},
        config={"run_name": "agent1b", "tags": ["agent1b"], "metadata": {"run_id": run_id},
                "max_concurrency": a1b.MAX_CONCURRENT_CLAUDE_CALLS, "recursion_limit": 50},
    )


def mermaid() -> str:
    """Diagram source for the README (no network/keys needed)."""
    graph = build_graph(client=object(), fetcher=lambda url, timeout: (None, "n/a"), cfg=ReviewConfig())
    return graph.get_graph(xray=True).draw_mermaid()
