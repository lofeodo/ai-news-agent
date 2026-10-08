# agents/agent3_dedup_graph.py
#
# Duplicate removal for agent3's article selection, as a small LangGraph that
# runs once per (section, selection pass). Lives *inside* the agent (ADR 0001).
#
#   check_start --> resolve --> refill <--+ (one fallback per visit, until the section is
#        \             \          \-------+  back to its original size, the pool or the
#         \-------------\--------> finalize  iteration limit runs out)
#
# check_start sends every pick to the detector once; resolve keeps one member of
# each duplicate group; refill pulls ranked runners-up (agent3_compose.take_fallback)
# and checks each one against everything seen, until the section is back to its size.
# Any failure degrades to the original, undeduped picks: this never raises.

from dataclasses import dataclass, field
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

import tracing
from config import AGENT3_DEDUP_MODE, DEDUP_MAX_ITERATIONS, DEDUP_MODEL
from dedup import DedupConversation


@dataclass(frozen=True)
class DedupConfig:
    mode: str = AGENT3_DEDUP_MODE            # "graph" | "off"
    max_iterations: int = DEDUP_MAX_ITERATIONS
    model: str = DEDUP_MODEL


@dataclass
class DedupResult:
    picks: list
    audit: dict
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})


class DedupState(TypedDict, total=False):
    picks: list                 # currently kept
    used: list                  # every runner-up already tried
    numbered: list              # article by conversation number (starts as the original picks)
    kept_of: dict               # conversation number -> number of the kept article it duplicates
    iterations: int
    exhausted: bool
    error: str | None
    groups: list                # audit: duplicate groups found among the original picks
    fallbacks: list             # audit: one entry per runner-up checked


def _brief(article: dict) -> dict:
    return {"title": article.get("title"), "url": article.get("url")}


def _hn(article: dict) -> int:
    return article.get("hn_score") or -1


def keep_index(indices: tuple, articles: list) -> int:
    """Member of a duplicate group to keep: highest HN score, ties to the earliest (the model's best-first order)."""
    return max(indices, key=lambda i: (_hn(articles[i]), -i))


def build_dedup_graph(category: str, original: list, runners_up: list, conv: DedupConversation, cfg: DedupConfig):
    target = len(original)

    def check_start(state: DedupState) -> dict:
        try:
            groups = conv.start(original)
        except Exception as e:
            return {"error": str(e)}
        return {"groups": groups}

    def after_start(state: DedupState) -> str:
        return "finalize" if state.get("error") else "resolve"

    def resolve(state: DedupState) -> dict:
        kept_of, drop, audit = {}, set(), []
        for g in state["groups"]:
            keep = keep_index(g.indices, original)
            for i in g.indices:
                if i != keep:
                    drop.add(i)
                    kept_of[i] = keep
            audit.append({"kept": _brief(original[keep]),
                          "removed": [_brief(original[i]) for i in g.indices if i != keep],
                          "reason": g.reason})
        picks = [a for i, a in enumerate(original) if i not in drop]
        return {"picks": picks, "kept_of": kept_of, "groups": audit,
                "used": [original[i] for i in sorted(drop)]}

    def route(state: DedupState) -> str:
        if state.get("error") or state.get("exhausted"):
            return "finalize"
        if state["iterations"] >= cfg.max_iterations or len(state["picks"]) >= target:
            return "finalize"
        return "refill"

    def refill(state: DedupState) -> dict:
        picks, used = state["picks"], state["used"]
        from agent3_compose import take_fallback   # lazy: agent3_compose imports this module
        cand = take_fallback(category, picks, runners_up, used)
        if cand is None:
            return {"exhausted": True}
        number = conv.count
        try:
            group = conv.add(cand)
        except Exception as e:
            return {"error": str(e)}
        numbered = state["numbered"] + [cand]
        kept_of = dict(state["kept_of"])
        entry = {**_brief(cand), "accepted": group is None}
        if group is None:
            return {"picks": picks + [cand], "used": used + [cand], "numbered": numbered,
                    "iterations": state["iterations"] + 1, "fallbacks": state["fallbacks"] + [entry]}
        # the match may be an article already removed: redirect to the one that was kept in its place
        others = [m for m in group.indices if m != number]
        rep = kept_of.get(others[0], others[0])
        kept_of[number] = rep
        entry.update(duplicate_of=_brief(numbered[rep]), reason=group.reason)
        return {"used": used + [cand], "numbered": numbered, "kept_of": kept_of,
                "iterations": state["iterations"] + 1, "fallbacks": state["fallbacks"] + [entry]}

    def finalize(state: DedupState) -> dict:
        return {}

    g = StateGraph(DedupState)
    g.add_node("check_start", check_start)
    g.add_node("resolve", resolve)
    g.add_node("refill", refill)
    g.add_node("finalize", finalize)
    g.add_edge(START, "check_start")
    g.add_conditional_edges("check_start", after_start, {"resolve": "resolve", "finalize": "finalize"})
    g.add_conditional_edges("resolve", route, {"refill": "refill", "finalize": "finalize"})
    g.add_conditional_edges("refill", route, {"refill": "refill", "finalize": "finalize"})
    g.add_edge("finalize", END)
    return g.compile()


def dedup_section(category: str, picks: list, runners_up: list, client, create: Callable | None = None,
                  cfg: DedupConfig | None = None, run_id: str = "", pass_name: str = "") -> DedupResult:
    """Remove duplicates from one section's picks, refilling from `runners_up`. Never raises."""
    cfg = cfg or DedupConfig()
    audit = {"category": category, "pass": pass_name, "status": "ok", "before": len(picks),
             "groups": [], "fallbacks": [], "error": None}
    if cfg.mode == "off" or len(picks) < 2:
        audit.update(status="off" if cfg.mode == "off" else "skipped", after=len(picks))
        return DedupResult(list(picks), audit)

    conv = DedupConversation(client, category, model=cfg.model, create=create)
    try:
        tracing.configure()
        graph = build_dedup_graph(category, list(picks), list(runners_up), conv, cfg)
        final = graph.invoke(
            {"picks": list(picks), "used": [], "numbered": list(picks), "kept_of": {}, "iterations": 0,
             "exhausted": False, "error": None, "groups": [], "fallbacks": []},
            config={"run_name": "agent3_dedup", "tags": ["agent3", "dedup"],
                    "metadata": {"run_id": run_id, "category": category, "pass": pass_name}},
        )
        error = final.get("error")
    except Exception as e:           # a graph-level failure is still only a degraded dedup
        final, error = {}, str(e)

    if error:
        print(f"  [dedup] '{category}' ({pass_name}): degraded to undeduped selection: {error}")
        audit.update(status="degraded", error=error, after=len(picks))
        return DedupResult(list(picks), audit, dict(conv.usage))

    result = final["picks"]
    audit.update(groups=final["groups"], fallbacks=final["fallbacks"], after=len(result))
    removed = sum(len(g["removed"]) for g in final["groups"])
    if removed:
        print(f"  [dedup] '{category}' ({pass_name}): removed {removed}, "
              f"{sum(1 for f in final['fallbacks'] if f['accepted'])} fallback(s) added")
    return DedupResult(result, audit, dict(conv.usage))
