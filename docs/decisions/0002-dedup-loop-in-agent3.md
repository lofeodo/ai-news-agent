# 0002 — Duplicate removal as a graph inside agent3

Status: accepted (2026-10-08)

## Context

agent3 picks each section's articles with one Haiku call, and two outlets
covering the same event often both get through. A prompt sentence was not
enough. Removing a duplicate leaves a gap, so the fix needs a loop: detect,
remove, pull a replacement, check the replacement.

## Decision

A small LangGraph per (section, selection pass), `agents/agent3_dedup_graph.py`,
inside agent3 (same reasoning as ADR 0001: Pub/Sub, the counter and the
watchdog are untouched). Nodes: `check_start` (one call with every pick) ->
`resolve` -> `refill` (one ranked runner-up per visit, checked through the
multi-turn `DedupConversation`) -> `finalize`.

- **Keep policy:** in a duplicate group, keep the highest HN score, ties to the
  earliest pick (the selection model lists its best first).
- **Removed articles stay in the conversation**, so a fallback can be flagged
  against a removed article's number. Such a match is redirected to the article
  that was kept in its place and the fallback is dropped.
- **Refill only restores the original size**, never adds beyond it, so a model
  that chose fewer picks than the cap is not padded.
- **Degrade, never fail:** any error returns the original undeduped picks with
  audit status `degraded`. A dedup problem must not stop the newsletter.
- **Rollback:** `AGENT3_DEDUP_MODE=off` skips the graph without a redeploy.
- **Audit:** `data/agent3_dedup_log.json`, Firestore `agent3_audits/{run_id}`,
  and a counts-only `agent3_dedup_summary` on the run doc (the run doc is near
  Firestore's 1 MiB cap).

## Consequences

Up to one extra Haiku call per section and pass, plus one per fallback checked.
Dedup quality is unmeasured until the eval (roadmap Steps 5-6).
