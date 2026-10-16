# Plan F: Custom topic sourcing

Part of the [newsletter platform](newsletter-platform.md) (roadmap **F**). Fully parallel: it produces custom pools
(contract C5) from the custom section topics people have saved, and needs no other roadmap. Roadmap E reads the pools
when it renders custom newsletters. Branch `feat/custom-topics`, one PR, one commit per step. The worker finishes
with `/ship`.

**Goal.** When a member adds a custom section such as "SpaceX product launches & mission updates", they should get
real, recent, summarized articles about it every week. This roadmap builds a weekly job that collects every distinct
custom topic, finds and filters articles for each, summarizes the best ones and stores them as a reusable pool, under
a hard weekly budget so the cost grows with the number of distinct topics, never with the number of readers.

## Decisions already made

From the master plan: cost scales with content; Claude calls once per distinct custom topic per week; contract C5.
Planner's assumptions (the worker may refine them and record changes in the PR):

- **Where topics come from:** the existing premium `users/{uid}.section_config.custom_sections[].refined_topic`
  (saved today by `sections.html`, never sourced so far) and, once roadmap E exists, `custom_section` blocks in
  `custom_newsletters/*` definitions (read the collection if present; absent = no topics from it). Normalized
  (lowercase, whitespace collapsed), de-duplicated, hashed per C5. Topics used by more readers go first.
- **Caps:** `CUSTOM_TOPICS_MAX` (default 50) distinct topics per week and `CUSTOM_TOPICS_MAX_USD` (default $2.00)
  per week, checked before each topic using the estimate from `pricing.py`; when either runs out, the remaining
  topics get no pool this week (the renderer drops the section with a note, per roadmap B).
- **Sources, no new paid services:** for each topic, a one-time *source plan* (one Haiku call when a topic is first
  seen, cached in `custom_queries/{hash}`): up to 3 Hacker News search queries and 1 NewsAPI query string. Weekly
  fetch reuses `fetch_hn_articles` and `fetch_newsapi_articles` from agent1b and its `prefilter` (paywall, social,
  non-Latin) without modifying them.
- **NewsAPI quota:** the job runs Saturday 9:00 AM Toronto (a different day from every topic run) and stops making
  NewsAPI calls after `CUSTOM_TOPICS_NEWSAPI_MAX_REQUESTS` (default 40), continuing with Hacker News only; the worker
  documents the NewsAPI plan limit found in the code/docs and the deploy request asks the owner to confirm the plan.
- **Relevance, then summaries:** one Haiku tool-use call per topic scores up to 40 candidate articles 1 to 5 for
  relevance to the topic (titles + first ~300 characters, existing prompt-injection guard); keep score ≥ 4, drop
  near-duplicate titles, rank by score then recency; summarize the top 5 picks + 3 runners-up with agent2b's
  `summarize_with_text` and the generic summary prompt, using the shared `article_fetch.fetch_article_text`.
- **Web search is off by default.** An optional fallback using Claude's server-side web search tool for topics with
  fewer than 3 relevant articles sits behind `CUSTOM_TOPICS_WEB_SEARCH=false`; the worker checks current web-search
  pricing with the `claude-api` skill before writing it, and its cost counts against the same weekly budget. If it
  would take more than one step to do well, it becomes a follow-up instead.
- **Its own Cloud Run service,** `agent-custom-topics` (`AGENT_NAME=custom_topics` in `main.py`'s registry, so it
  gets the watchdog and failure recording), triggered by its own Scheduler job, outside the Pub/Sub chain, like the
  health check. Summary of each weekly run in `custom_topic_runs/{week}`.
- **Privacy:** pools and run summaries contain topics and articles only, never who asked for them; reader counts per
  topic are kept as numbers in the run summary only.
- **Model:** the project's configured Haiku model (`config.py`), like every other agent.

## Files this roadmap owns (parallel work)

Owns: `agents/custom_topics.py` (new: collect, plan, fetch, filter, summarize, store), `agents/agent_custom_topics.py`
(new: `run(run_id)` entry point), `agents/custom_topics_report.py` (new), `prompts/custom_topics/` (new),
`custom_topic_tool.py`-style tool schemas inside the new module, `tests/test_custom_topics*.py`. Small additive edits:
`main.py` (registry entry), `cloudbuild.yaml` (one more image), `agents/agent_healthcheck.py` (one call in the last
code step, after rebasing on A if merged). Does **not** modify agent1b, agent2b or `article_fetch.py` (imports only),
`agents/agent_subscriptions.py`, `agents/agent3_compose.py`, any page.

## Steps

Run `venv\Scripts\python -m pytest -q` before every commit. Tests use the fake Firestore and a fake Claude client; no
network.

1. **Collect topics.** `custom_topics.collect(db)` → ranked list of `{query, hash, readers}` from both sources in the
   decisions, normalized, capped by `CUSTOM_TOPICS_MAX`. *Test:* duplicates across users merge, ranking by readers,
   missing `custom_newsletters` collection is fine, angle brackets or empty strings are skipped.

2. **Source plan.** `plan_sources(client, query)` (tool use: `hn_queries` 1 to 3 strings, `newsapi_query` string, each
   length-limited and stripped of quotes the APIs cannot take), cached in `custom_queries/{hash}` with `created_at`;
   reused every week. *Test:* fake client; cache hit makes no call; malformed tool output falls back to the raw query.

3. **Fetch and prefilter.** `fetch_candidates(plan, lookback_hours)` using agent1b's fetchers and `prefilter`, with
   the NewsAPI request counter; at most 40 candidates. *Test:* fetchers monkeypatched; counter stops NewsAPI at the
   limit; HN-only path.

4. **Relevance filter.** `score_relevance(client, query, candidates)` → kept, ranked list (decisions above).
   *Test:* fake client scores; near-duplicate titles collapse; an injection-style title stays data.

5. **Summarize and store.** `build_pool(...)` summarizes picks and runners-up, writes
   `custom_pools/{hash}__{week}` in the C5 shape (articles only with summaries; `week` = the date of the Monday the
   pool is for, ISO `YYYY-MM-DD`, so a Saturday run serves the next Monday's send), local mode writes
   `data/custom_pools/*.json`. *Test:* C5 shape; an article whose summary fails is skipped, not fatal.

6. **Weekly job with budget.** `agent_custom_topics.run(run_id)`: collect → for each topic, estimate cost, stop when
   the budget is reached → plan → fetch → filter → pool; per-topic failures logged and counted, never fatal; writes
   `custom_topic_runs/{week}` (`topics_seen`, `pooled`, `skipped_budget`, `skipped_no_articles`, `failed`, `usd`,
   `newsapi_requests`); `_record_failure` on an unexpected top-level error. Registered in `main.py`. *Test:* budget
   exhaustion mid-list; one topic raising; summary doc fields.

7. **Optional web-search fallback** (see decisions), off by default. *Test:* fake client; flag off makes no call.

8. **Health check section.** `custom_topics_report.section(db)`: informational "Custom sections" lines from the
   latest `custom_topic_runs` doc (pooled N of M, budget used, failures); flags nothing in v1. One call added to the
   health check's send report. *Test:* formatting; no run doc yet.

9. **Offline check of quality and cost.** `evals/run_custom_topics_check.py` (not collected by pytest): runs the
   pipeline locally on 10 sample topics (e.g. SpaceX launches, Nintendo Switch 2 games, Bank of Canada decisions,
   Bambu Lab printers, Gloomhaven expansions, open-source LLMs, EV batteries, Montreal tech startups, chess, Rust
   language releases) with the owner's local keys, writes `evals/results/custom_topics_<date>.json` with pooled
   counts and cost per topic. Skip and say so in the PR if no key is available locally. The worker reads the pools and
   notes in the PR any topic whose picks are clearly off.

10. **Docs.** `docs/architecture.md` (Custom topic sourcing), `docs/configuration.md` (the env vars above),
    `docs/monitoring.md` (Custom sections line), CLAUDE.md (new service, Saturday job), `docs/repository-structure.md`.

## UI check

No (no page changes; the studio shows pools through roadmap E).

## Deploy needs (agents never deploy; this feeds the deploy request)

New service **agent-custom-topics** (image from `cloudbuild.yaml`; secrets `anthropic-api-key`, `news-api-key`,
`squid-proxy-url` mounted like agent1b/agent2b; same service account), then **healthcheck** (Custom sections line).
Owner actions: create the service and a Scheduler job Saturday 9:00 AM America/Toronto (`POST /`), confirm the
NewsAPI plan's daily limit, optionally a Firestore TTL on `custom_pools.expires_at` (the worker sets 60 days).
Timing: any weekday; it runs only on Saturdays and touches nothing readers receive until roadmap E renders the pools.
Rollback: pause the Scheduler job.

## Out of scope / follow-ups

- Rendering pools into emails and the studio preview (roadmaps B and E).
- RSS or Google News feeds per custom topic; Reddit.
- Re-planning a topic's sources automatically when it finds nothing for several weeks.
- French custom topics.
