# Plan: Multi-topic newsletter engine

**Goal.** Turn the weekly AI newsletter pipeline into an engine that can write a newsletter on any subject, chosen by
a *topic profile* (sources, categories, prompts, look). This roadmap adds four new topics (finance, video games,
board games, 3D printing) that produce private draft issues on demand, while the AI newsletter and its subscribers
stay exactly as they are.

One branch (`feat/multi-topic`), one PR, one commit per step below. The worker finishes with `/ship`.

## Decisions already made

Owner's answers (2026-10-16):

- **Scope: engine + private drafts for all four topics.** No public signup, landing pages or sending for the new
  topics in this PR. That is the next roadmap, after the owner has read a few drafts.
- **The two owner-delegated niches are board games (tabletop) and 3D printing.** Chosen because both have very large,
  active communities (Reddit, BoardGameGeek, Printables/Prusa forums) that accept sharing a free project, plenty of
  news sources reachable without paid APIs, and no dominant weekly digest.
- **Debug runs only for now.** New topics run only when started by hand, as debug runs. They will eventually run
  weekly; the code must make that a configuration step (a Scheduler job with `?topic=<id>&kind=release`), but this
  PR keeps release runs blocked for non-AI topics.

Planner's assumptions (the worker may refine these and record changes in the PR):

- **The AI newsletter must not change at all.** With no `topic` given, every agent behaves byte-for-byte as today:
  same queries, categories, prompts, HTML, Firestore fields. A regression test pins this (step 2).
- **Topic id travels on the run doc.** The orchestrator writes `topic` on `pipeline_runs/{run_id}`
  (`POST /?topic=<id>&kind=debug`, default `ai`). Pub/Sub payloads stay `{run_id}` only; each agent reads `topic`
  from the run doc (local mode: from an orchestrator argument / env `TOPIC`). A doc without `topic` is `ai`.
- **Non-AI topics may only be `debug`** in this PR. The orchestrator returns 400 for `kind=release` with a non-AI
  topic. This is the safety net that keeps new topics away from agent4, `/preview`, `/issues` and the send check.
- **Every "latest run" lookup is AI-only.** `run_kind.latest_run`, `run_kind.list_releases`, the healthcheck's
  latest-doc query, `drift_history` and anything else that orders `pipeline_runs` by `started_at` must ignore
  docs whose topic is not `ai`, filtered in code (no new Firestore index; missing field = `ai`). Raise scan limits
  if needed so a burst of topic debug runs cannot push the AI run out of the window. Otherwise a finance debug run
  started after the AI draft could be picked up by agent4-test or the health check.
- **Topic profiles are YAML files in `topics/<id>.yaml`**, validated by `agents/topics.py` (PyYAML is already a
  dependency). A profile holds: `id`, `name` (newsletter title), `tagline`, `audience` (one sentence used by
  prompts), `accent_color`, `languages` (`[en]` for all new topics), `variants` (`[0_0]` for all new topics; AI
  keeps its four), `categories` (ordered list with a one-line definition each and an optional cap), `sources`
  (see next point), `spotlight` (`papers` for AI, `none` for the rest), and `prompts` (paths; AI points at today's
  files, others at generic templates).
- **Sources are pluggable adapters** returning the same article dict agent1b already uses:
  - `hn` (existing Algolia fetch; AI keeps its current top-1000 call; other topics may pass a query string, e.g.
    3D printing `"3d print" OR "3d printer"`),
  - `newsapi` (existing; per-topic query list),
  - `rss` (new, `feedparser`, already a dependency; per-feed `name`, `url`, optional `max_items`),
  - community signal: an article may carry `points` + `points_source` (HN today). Agent3's "hn: unknown, not
    penalized" labelling generalizes to "community score unknown". Reddit is **out of scope** (needs an app
    registration by the owner and Reddit blocks GCP IPs); see follow-ups.
- **Spotlight `none`** means agent1a and agent2a do nothing for that run but still record their stage
  (`scored_papers`/`paper_summaries` empty with a `skipped: "no spotlight for topic"` marker) and still count in
  `agent2_completions`, so the existing Pub/Sub chain, stage counter, watchdog and retries stay untouched. Agent3's
  "refuse an issue with no summarized paper" applies only when the topic's spotlight is `papers`.
- **Variants:** a topic with `variants: [0_0]` gets one rendered variant only (smaller run doc; the admin preview
  already falls back to `0_0`). French/Canada passes are skipped for those topics.
- **Branding:** newsletter title, tagline and accent color come from the profile; layout and email template are
  shared. No new logos.
- **Paywalls:** `PAYWALLED_DOMAINS` stays global; finance adds its own extra list in the profile (e.g.
  `barrons.com`, `investors.com`, `seekingalpha.com`, `marketwatch.com` premium paths if they turn out empty).
- **Cost guard:** each topic run logs its Claude usage through the existing usage/pricing helpers. Profiles may set
  `max_articles` (cap after prefilter, default 600) so a feed-heavy topic cannot blow up the categorize stage.

## Starting topic profiles (worker verifies every feed URL once and drops dead ones)

Categories are a starting point; the worker may tune them after the first draft and note it.

| Topic | Categories (7 max) | Sources |
|---|---|---|
| **finance** — "Markets & Money Weekly" for retail investors and personal-finance readers | Markets & Macro · Central Banks & Rates · Earnings & Companies · Personal Finance · Crypto & Fintech · Regulation & Policy · Canada | NewsAPI (markets, Fed/BoC, earnings, personal finance, crypto/fintech, Canada markets); RSS: CNBC, MarketWatch top stories, Yahoo Finance, Investopedia news, CoinDesk, The Globe and Mail business is paywalled (skip); HN query `stock OR "interest rates" OR fintech` |
| **gaming** — "Video Games Weekly" | Releases & Reviews · Industry & Business · Platforms & Hardware · Indie Spotlight · Esports & Competitive · Deals & Free Games · Layoffs & Labor | NewsAPI (game releases, studios/publishers, Nintendo/PlayStation/Xbox/Steam); RSS: Eurogamer, PC Gamer, Rock Paper Shotgun, VGC, GamesIndustry.biz, Nintendo Life, Push Square, Polygon |
| **boardgames** — "Tabletop Weekly" | New Releases · Crowdfunding (Kickstarter/Gamefound) · Reviews & Previews · Industry & Publishers · Events & Conventions · Digital Adaptations · Community & Culture | RSS: BoardGameGeek news, Dicebreaker, BoardGameWire, Meeple Mountain, Tabletop Gaming News, Polygon tabletop; NewsAPI (`"board game" OR tabletop OR Kickstarter board`); optional BGG XML "hot" list as community signal (worker decides; skip if it slows the run) |
| **printing3d** — "Layer Lines" (3D printing weekly) | New Printers & Hardware · Firmware & Software · Materials & Filament · Projects & Prints · Industry & Business · Guides & Tips · Community & Policy | RSS: 3DPrint.com, Fabbaloo, All3DP, Hackaday (3D printing tag), Tom's Hardware 3D printing, Prusa blog, Bambu Lab blog; HN query; NewsAPI (`"3D printing" OR "3D printer"`) |

## Steps

Each step is one commit; run `venv\Scripts\python -m pytest -q` before every commit (all tests stay green).

1. **Topic profile loader.** `agents/topics.py` (load + validate YAML, `get_topic(id)`, `DEFAULT_TOPIC = "ai"`)
   and `topics/ai.yaml` describing today's AI setup exactly (queries, categories from `filter_tool.py`, section caps,
   prompt paths, four variants, spotlight `papers`). Nothing uses it yet.
   *Done:* loader rejects bad profiles (missing fields, unknown spotlight, >7 categories, duplicate category names).
   *Test:* new `tests/test_topics.py`.

2. **AI equivalence pin.** Test that the AI profile reproduces today's constants: `NEWSAPI_QUERIES`, `CATEGORIES`,
   `SECTION_CAPS`, `DEFAULT_SECTIONS` order, and the prompt files' text. This test guards every later step.
   *Test:* `tests/test_topics_ai_equivalence.py`.

3. **Topic on the run doc + orchestrator.** `orchestrator.py` / `main.py` accept `topic` (default `ai`), validate it
   against the loaded profiles, write it on the run doc, and reject `kind=release` for non-AI topics (400). Local
   mode: `python orchestrator.py --topic finance` (and env `TOPIC`). Shared helper `topics.topic_for_run(db, run_id)`
   (missing field = `ai`).
   *Test:* orchestrator/main tests with the fake Firestore (`tests/fakes_firestore.py`).

4. **AI-only latest-run lookups.** Filter non-AI docs out of `run_kind.latest_run`, `run_kind.list_releases`, the
   healthcheck's latest-doc query, `drift_history`, and any other `pipeline_runs` scan (grep `order_by("started_at"`).
   *Test:* extend the existing run_kind/healthcheck/archive tests with a newer `topic: finance` debug doc and assert
   the AI doc still wins.

5. **Source adapters.** Move the HN and NewsAPI fetchers behind a small adapter interface (`agents/sources/` or
   `agents/news_sources.py`), add the RSS adapter (`feedparser`, lookback window = `LOOKBACK_HOURS`, proxy env
   honored, per-feed failures logged and skipped, never fatal). AI's path calls the same HN/NewsAPI code as before.
   *Test:* RSS adapter on a fixture feed (no network); AI fetch still calls HN+NewsAPI with identical arguments.

6. **agent1b reads the profile.** `fetch` node builds the source list from the topic; `prefilter` uses global +
   topic paywall lists and `max_articles`; `language_filter` uses the topic's languages; `categorize` builds the
   tool enum and prompt from the topic's categories (generic template `prompts/generic/news_filter_prompt.txt` with
   `{topic_name}`, `{audience}`, `{category_definitions}`). Single-pass mode stays AI-only (raise for other topics).
   *Test:* `test_agent1b_graph.py` gains a non-AI topic case with fake sources and a fake Claude client.

7. **Spotlight `none`.** agent1a and agent2a skip work for such topics, write the empty stage markers, and still
   count/publish so agent3 is triggered. Agent3's no-paper refusal only applies to `spotlight: papers`.
   *Test:* stage-counter test for a `none` topic; agent3 composes without a paper card.

8. **agent2b + agent3 read the profile.** agent2b uses a generic summary prompt with topic fields (AI keeps its
   files). agent3: generic selection/intro prompts, topic categories and caps, title/tagline/accent color in the
   template, "community score unknown" labelling, only the profile's variants rendered, dedup unchanged. The share
   strip and premium reordering stay as they are (AI only in practice).
   *Test:* compose a fake `gaming` run end-to-end from fixture summaries; assert one variant, topic title, no
   `{{...}}` leftovers besides the two footer placeholders, AI fixture output unchanged.

9. **Four topic profiles.** `topics/finance.yaml`, `topics/gaming.yaml`, `topics/boardgames.yaml`,
   `topics/printing3d.yaml` from the table above. The worker fetches each RSS URL once (plain `requests`, not via
   the pipeline) and removes feeds that fail or are empty; it records which feeds were kept in the PR.
   *Test:* all profiles load; a test asserts every profile's categories fit the tool enum limits.

10. **Local dry run per topic.** With the owner's local keys (already in the developer's environment, never copied
    anywhere), run `python orchestrator.py --topic <id>` for each new topic, read the HTML in `data/`, and tune
    categories/prompts where the draft is clearly off (e.g. an empty category). Note per-topic Claude cost from the
    usage log in the PR. If no key is available locally, skip and say so in the PR; this is not a blocker.
    *Done:* four local drafts look like real newsletters; any tuning committed separately.

11. **Admin topic picker (UI).** The admin-only Debug run toggle on `issues.html` gets a topic dropdown;
    `GET /auth/admin/preview` takes `topic=` (with `run=latest`, picks the newest run of that topic, debug included).
    Public pages are unchanged.
    *Test:* `test_account_routes.py` / admin preview tests for the new parameter (non-admin still 403/404).
    *UI check:* yes, desktop + Android + iOS screenshots against a mock API before this commit.

12. **Docs.** CLAUDE.md (What this project does, topic profiles, `?topic=`, release-blocked rule),
    `docs/architecture.md`, `docs/configuration.md` (profile format, how to add a topic), `docs/runbook.md` (start a
    topic debug run: call the orchestrator with `?topic=<id>`), `docs/repository-structure.md`. README only if its
    one-line summary is now wrong.

## UI check

Yes, step 11 only (admin debug toggle). Desktop, Android (Pixel 7) and iOS (iPhone 15) screenshots before that
commit. The email template change in step 8 is checked by reading the four local drafts (step 10) in a browser at
desktop and phone width.

## Deploy needs (agents never deploy; this feeds the deploy request)

Order: **agent1a, agent1b, agent2a, agent2b, agent3** (read `topic`, behave identically for AI), then
**orchestrator** (accepts `?topic=`), then **healthcheck** and **agent4** (AI-only lookups), then
**agent-subscriptions** (admin topic preview), then **hosting** (admin dropdown). All from one Cloud Build image set
(`gcloud builds submit --config cloudbuild.yaml`), then `gcloud run services update <svc> --image ...` per service.

- No new secrets: NewsAPI and Anthropic keys are already mounted on the agents that need them. The RSS fetch goes
  through the existing Squid proxy on agent1b.
- No Scheduler changes in this PR. First topic debug run after deploy: call the orchestrator with
  `?topic=finance` (no `kind`, so debug), then view it via the admin Debug run toggle.
- **Timing:** deploy Monday after the 7:10 AM send check, or any weekday; never Sunday before the noon draft or
  between the draft and the Monday send. Because the AI path is pinned byte-identical, risk is low; rollback is
  re-deploying the previous image per service.

## Out of scope / follow-ups

- Public signup, landing page and archive per topic; subscriber `topics` field and per-topic sends from agent4;
  the per-topic health check. (Next roadmap: "multi-topic subscriptions".)
- Weekly Scheduler jobs per topic and allowing `kind=release` for non-AI topics.
- Reddit as a source/community signal (owner registers a Reddit app; GCP IPs are blocked without OAuth).
- Topic spotlights (e.g. BGG "hot" game of the week, Kickstarter of the week, a printer review, a game of the
  week), French/Canada variants for new topics, premium custom sections for new topics.
- Renaming the project/brand from an AI newsletter to a multi-topic engine (README, banner, domain).
- Where to share each newsletter (r/boardgames self-promo rules, BGG forums, r/3Dprinting, r/BambuLab, r/pcgaming,
  r/personalfinancecanada, etc.): a short launch checklist for the owner when signups exist.
