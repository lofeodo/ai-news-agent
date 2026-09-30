# 0001 — LangGraph inside agents, not across services

Status: accepted (2026-09-30)

## Context

The pipeline is six Cloud Run services chained by Pub/Sub, with a Firestore
transaction (`agent2_completions`) as the join between agent 2a and 2b. One
stage, agent 1b, has genuine internal control flow: a first-pass categorization,
a confidence-based branch, and a tool-calling review loop. We wanted that logic
expressed as an explicit graph (nodes, conditional edges, loop) and asked whether
LangGraph should also replace the cross-service orchestration.

## Decision

Use LangGraph **inside** an agent (agent 1b first) and leave the cross-service
orchestration — Pub/Sub, the Firestore counter, `main.py`'s watchdog and
`_ISOLATED_RETRIES` — exactly as it is. Local and cloud mode run the same
in-agent graph; there is no pipeline-level LangGraph wrapper for local mode.

## Why not use LangGraph across services

- **A LangGraph join is in-process.** A fan-in node waits for parallel branches
  *inside one running graph*. agent2a and agent2b run in different Cloud Run
  instances (and can run at different times, or be retried independently), so
  nothing in memory can wait for both. The Firestore transaction does that job:
  it is the shared state that survives instance boundaries.
- **Replacing it means one of two things**, both worse:
  1. Collapse the stages into a single runner/service, losing independent
     retries, per-stage scaling, per-stage timeouts, and the crash isolation
     that agent2b relies on (a native abort kills only its child process and is
     retried); or
  2. Add a persistent checkpointer (e.g. Postgres/Firestore-backed) plus a
     resume mechanism, so separate invocations can continue one graph run. That
     is new infrastructure that re-implements what Pub/Sub + a counter already
     do, with more moving parts and no new capability.
- The existing layer is deliberately simple, observable in Firestore, and
  covered by the health check. The graph adds value where there is branching
  and looping, not where there is a linear hand-off.

## Consequences

- agent 1b gains an explicit, testable, traceable graph (see README).
- Only agent 1b's import path pulls in `langgraph`/`langsmith` (imported lazily in
  `run()`), though the shared Docker image grows for all services.
- `AGENT1B_MODE=single_pass` keeps the original implementation callable as a
  rollback and as a baseline for later evaluation.
- Other agents (e.g. a future agent 2b verify loop) can adopt the same in-agent
  pattern without touching the cross-service design.
