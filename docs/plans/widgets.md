# Plan C: Widgets

Part of the [newsletter platform](newsletter-platform.md) (roadmap **C**). Can run in parallel with A, B, D and F;
only its integration step (step 15) binds to roadmap B, and it has a fallback if B has not merged. Branch
`feat/widgets`, one PR, one commit per step. The worker finishes with `/ship`.

**Goal.** Give every newsletter small, useful data blocks next to the articles, the way the AI newsletter already has
its paper of the week: a market snapshot and rates board for finance, free games and the week's most-played for video
games, the BoardGameGeek hot list for board games, a slicer and firmware release tracker for 3D printing, and so on.
Widgets are built once as plug-ins that every newsletter (ready-made or custom) can place, they never break an issue
when their data source fails, and premium members can later add personal ones such as a stock watchlist.

## Decisions already made

From the master plan: widgets follow contract C4; edition widgets are produced once per topic run, personal widgets
per distinct configuration with a cache; a failing widget is dropped from that week's email and never fails a run or
a send; AI readers see no change. Planner's assumptions (the worker may refine them and record changes in the PR):

### What a widget is

| Kind | Produced | Example | Who can add it |
|---|---|---|---|
| **Edition widget**, external data | once per topic run, by agent3 before rendering | market snapshot, free games, release tracker | the topic's profile; any custom newsletter |
| **Edition widget**, derived from the edition | once per topic run, from that week's picks, at most one Haiku call | number of the week, crowdfunding watch | same |
| **Personal widget** | at send (and preview) time, per distinct config, cached for the week | stock watchlist | premium custom newsletters only |
| **Pipeline widget** | by existing agents | paper of the week (agent1a + agent2a) | AI profile; any custom newsletter |

### Rules every widget follows

- **Bounded:** `produce` gets a per-widget timeout (20 s) and all edition widgets of a run share a 90 s budget, run in
  a small thread pool; data is JSON, at most 20 KB; the rendered fragment at most 8 KB (keeps emails under the Gmail
  clip limit, contract C7).
- **Untrusted data:** everything from a third party or a model is escaped; links go through the existing `_safe_url`;
  derived widgets use the existing prompt-injection guard and treat model output as data, never markup.
- **Email-safe look:** one shared primitives module (card shell matching the newsletter's dark/cream style, stat row,
  small data table, a text sparkline using `▁▂▃▄▅▆▇█`, deltas with both colour and an arrow and sign so they read
  without colour, dark-mode safe colours, stacks at phone width). Real data tables use proper header cells; layout
  tables use `role="presentation"`.
- **Honest data:** every widget shows its source and "as of" time in a small attribution line; finance widgets add
  "For information only, not financial advice"; a widget never shows stale data as current (data older than its
  `max_age` is dropped).
- **Licences first:** a data source is used only after its terms are checked for use in an emailed newsletter that may
  later be part of a paid service (commercial use, attribution, caching, rate limits). Sources needing an API key or
  an account are built and tested but **ship disabled**; the deploy request asks the owner to register and store the
  key (agents never handle secrets). A missing key shows as "skipped: no key" in the health check.
- **Costs:** derived widgets make at most one Haiku call per edition and log usage through the existing usage helpers;
  personal widgets are cached per `(widget, config_hash, week)` so 100 readers with the same tickers cost one fetch.
- **Network:** agent3 already has the Squid proxy mounted; widgets use the proxy env like the other fetchers and send
  a descriptive User-Agent with a contact URL.

### Starting catalog (step 1 verifies each source; drop or swap what fails)

| Topic | Widget | Kind | Data source candidates | Notes found while planning (2026-10-16) |
|---|---|---|---|---|
| AI | `paper_spotlight` Paper of the week | pipeline | existing run doc | Port of today's card, byte-identical |
| AI | `trending_models` Trending on Hugging Face | edition | HF Hub API `api/models?sort=trendingScore` | No key expected; verify terms |
| finance | `market_snapshot` The week in markets | edition | Bank of Canada Valet (CAD/USD, Canada yields), FRED (US Treasury yields, WTI), CoinGecko (bitcoin) | **Stock indices (S&P 500, Nasdaq, TSX) are exchange-licensed; free APIs do not clearly allow newsletter redistribution.** v1 shows open-licence figures only; indices need an owner decision (licensed source, possibly paid). FRED needs a free key. |
| finance | `rates_board` Central bank rates | edition | BoC Valet, FRED (Fed target range), ECB Data Portal; next meeting dates from a yearly YAML file | The YAML needs one owner update per year (a reminder goes in the runbook) |
| finance | `week_ahead` The week ahead | edition | FRED release dates (CPI, jobs, GDP) + the meeting YAML | Earnings calendars need a paid or unclear source: left out of v1 |
| finance | `stock_watchlist` My watchlist | personal | same decision as indices | Blocked on the index-data licence decision; framework support ships, provider waits |
| gaming | `free_games` Free this week | edition | Epic Games Store `freeGamesPromotions` (public, undocumented), GamerPower API | Epic endpoint is undocumented and may change: parse defensively, fall back to GamerPower |
| gaming | `most_played` Most played on Steam | edition | Steam Web API charts (`ISteamChartsService`) | Could not confirm the method from public docs while planning: step 1 decides |
| gaming | `deals` Best deals | edition | CheapShark API | Check attribution requirement |
| gaming | `out_this_week` Out this week | edition | IGDB (Twitch app credentials) or RAWG (key, attribution link) | Needs an owner-registered key: ships disabled |
| boardgames | `bgg_hot` The BGG hot list | edition | BoardGameGeek XML API2 `/hot` | **Since July 2025 BGG requires a registered application and a Bearer token; registration is free for non-commercial use.** Needs an owner registration, and BGG's commercial terms must be asked about before premium is paid. Ships disabled. |
| boardgames | `crowdfunding_watch` On Kickstarter & Gamefound | derived | the edition's Crowdfunding section | No platform API; one Haiku call extracts name, platform, status and link from that week's picks, only what the articles say |
| printing3d | `release_tracker` New versions | edition | GitHub Releases API: PrusaSlicer, OrcaSlicer, Bambu Studio, Cura, Klipper, Marlin, Prusa-Firmware-Buddy, Creality Print | Unauthenticated limit is 60/hour per IP (8 calls per run is fine; optional token later); "new this week" badge |
| printing3d | `model_of_week` Model of the week | edition | Thingiverse API (app token) | Printables has no public API: not used. Ships disabled |
| any | `number_of_week` Number of the week | derived | the edition's picks | One striking number with the sentence it came from and its link; Haiku, about $0.002 a run |

## Files this roadmap owns (parallel work)

Owns: `agents/widgets/` (new package), `agents/admin_widgets.py` (new router), `agents/widget_report.py` (new),
`prompts/widgets/` (new), `public/newsletter/widgets.html` (new, admin-only), `docs/widgets.md` (new),
`agents/topics.py` (the `widgets:` key) and `topics/*.yaml` (`widgets:` entries only),
`tests/fixtures/widgets/`. Edits elsewhere are one-liners in the integration steps: `agents/agent3_compose.py` (step
15, after rebasing on B if merged), `main.py` (mount the admin router), `agents/agent_healthcheck.py` (step 16,
one call). Does **not** touch `agents/agent4_send.py` (personal widgets are wired into sending by roadmap E),
`agents/agent_subscriptions.py`, studio pages.

## Steps

Run `venv\Scripts\python -m pytest -q` before every commit. Tests never touch the network: each provider is tested on
a recorded response in `tests/fixtures/widgets/`.

1. **Source research.** For each catalog row: endpoint, key or account needed, terms (commercial use, attribution,
   caching, redistribution), rate limit, whether it answers through the proxy, and one recorded sample response saved
   as a fixture (strip anything personal). Record a keep / drop / needs-owner-key / needs-owner-decision verdict in
   `docs/widgets.md#sources`. Do not sign up for anything or accept terms on the owner's behalf. *Done:* table
   complete, fixtures committed.

2. **Framework.** `agents/widgets/base.py` (the C4 `Widget` class, `ProduceContext` with topic, run id, week, the
   edition's picks for derived widgets, an HTTP session with proxy and User-Agent, a usage logger), `registry.py`
   (`register`, `get`, `catalog(topic=None)`, `for_topic`), `config.py` (validate `config_fields`: tickers
   `^[A-Z0-9.\-]{1,12}$` max 10, choice, int with bounds, bool; `config_hash`), `runner.py`
   (`produce_edition_widgets(ctx, entries)` with timeouts, size caps and budget; returns data + per-widget status:
   ok / failed / skipped_no_key / timed_out). *Test:* `tests/test_widgets_framework.py` (timeout, oversized data,
   exception, missing key, budget exhaustion, config validation).

3. **Storage.** `agents/widgets/store.py`: edition data at `pipeline_runs/{run_id}/widgets/{widget_id}`
   (`{data, produced_at, status}`), `widgets_summary` (statuses only) on the run doc, local `data/widgets/*.json`;
   personal cache `widget_cache/{widget_id}__{config_hash}__{week}` with `expires_at` (8 days) and
   `get_or_produce_personal(db, widget, config, week)`. *Test:* fake Firestore.

4. **Email primitives.** `agents/widgets/ui.py`: card shell (takes the newsletter's accent), stat row, data table,
   text sparkline, delta formatting (arrow + sign + colour), attribution line, escaping and safe URLs reused from the
   renderer. *Test:* escaping, sparkline scaling, delta formatting, every primitive under its size budget.

5. **Paper of the week as a widget.** `widgets/paper_spotlight.py`: `produce` reads the run's `paper_summaries`,
   `render` returns exactly today's `render_paper_card` output, `sample_data` from the fixture paper. *Test:* render
   equals `render_paper_card` for the fixture papers (byte-identical).

6. **Profiles name their widgets.** `topics.py` parses an optional `widgets:` list of
   `{id, placement: after_intro | before_sections | after_sections, config}` (unknown id or bad config = profile
   error). `topics/ai.yaml` gets `paper_spotlight` at `after_intro`; the other profiles get none yet. *Test:*
   `test_topics.py` cases; the AI equivalence test stays green.

7. **`release_tracker`** (3D printing). Latest release per repo, version, date, "new this week" badge, link to the
   release page; pre-releases skipped. *Test:* fixture render; repo with no releases; API error.

8. **`free_games`** (gaming). Current and upcoming free games with end dates and store link; GamerPower fallback.
   *Test:* fixtures for both sources, defensive parsing of a changed payload.

9. **`most_played`** and **`deals`** (gaming), if step 1 kept them; one commit each. *Test:* fixtures.

10. **`trending_models`** (AI). Top 5 trending models with task and likes. Not added to the AI profile (AI readers
    see no change); available to custom newsletters. *Test:* fixture.

11. **`rates_board`** and **`week_ahead`** (finance), with `data/finance_calendar_<year>.yaml` (meeting and release
    dates, sourced and linked); FRED-backed parts ship disabled until the owner adds the key. One commit each.
    *Test:* fixtures; "no key" path.

12. **`market_snapshot`** (finance), open-licence series only (see the catalog); weekly change and a 5-day
    sparkline per series. Index levels only if the owner has decided on a licensed source (if not, record it as a
    follow-up). *Test:* fixtures; a missing series drops one row, not the widget.

13. **Derived widgets.** `number_of_week` and `crowdfunding_watch`, prompts in `prompts/widgets/`, tool-use output
    schema, injection guard, one call each, usage logged. *Test:* fake Claude client; injection fixture (an article
    telling the model to output a link) renders as plain text.

14. **Keyed providers, shipped disabled.** `bgg_hot`, `out_this_week`, `model_of_week` (each only if step 1 kept it),
    reading their key from env/Secret Manager names documented in `docs/widgets.md`; absent key = skipped. Personal
    `stock_watchlist` only if the index-data decision allows it; otherwise the framework's personal path is proven by
    a test-only personal widget. One commit each. *Test:* fixtures; no-key path.

15. **Integration into agent3.** agent3 produces the topic's edition widgets (profile `widgets:`) before rendering,
    stores them (step 3), and places their fragments. If roadmap B has merged: replace B's default `render_widget`
    with the registry and add `widget` blocks to `from_topic` from the profile; the edition's `widgets` list is
    filled. If B has not merged: a minimal hook in `compose_html` at the three placements. Either way AI output stays
    byte-identical (B's golden test if present, otherwise a golden added here). *Test:* a topic with two fake widgets
    (one failing) composes with one widget; the run doc has `widgets_summary`.

16. **Health check section.** `agents/widget_report.py` builds an informational "Widgets" section from
    `widgets_summary` (ok / failed / skipped_no_key per newsletter); one call added to the health check. Never flags
    the run as a problem in v1. *Test:* report formatting; healthcheck test with and without summaries.

17. **Admin widget gallery (UI).** `GET /auth/admin/widgets` (new router `agents/admin_widgets.py`, mounted for
    agent-subscriptions in `main.py`, admin-only like the debug preview) returns each widget's metadata, its
    `sample_data` render and, when available, its latest produced render. `public/newsletter/widgets.html` (admin
    only, not linked publicly) shows them in an email-width frame with a desktop/phone toggle. *Test:* route tests
    (non-admin 403/404). *UI check:* desktop, Android, iOS.

18. **Turn widgets on in the topic profiles.** After reviewing the gallery: one commit per topic adding the keyless
    widgets that passed step 1 (e.g. 3D printing `release_tracker`, gaming `free_games` + `most_played`, finance
    `rates_board` + `market_snapshot` if keyless parts exist, any topic `number_of_week`). Then a local dry run per
    changed topic (`python orchestrator.py --topic <id>`, owner's local keys, skip and say so if absent) and read the
    HTML at desktop and phone width.

19. **Docs.** `docs/widgets.md` (what a widget is, the rules above, the sources table, **how to add a widget** in five
    steps, the yearly finance calendar update), `docs/configuration.md` (`widgets:` profile key, widget keys),
    `docs/architecture.md`, CLAUDE.md (one paragraph), master plan if C4 changed.

## UI check

Yes: step 17 (admin gallery) and step 18 (local drafts with widgets) at desktop, Android (Pixel 7) and iOS
(iPhone 15). Flag overflow in data tables on phones.

## Deploy needs (agents never deploy; this feeds the deploy request)

**agent3** (produces and places widgets; AI output unchanged), **healthcheck** (Widgets section),
**agent-subscriptions** (admin gallery), **hosting** (gallery page). Owner actions, each optional and independent:
register keys for FRED, BoardGameGeek (ask BGG about commercial terms before premium is paid), IGDB or RAWG,
Thingiverse, and store each in Secret Manager mounted on agent3 (and later agent4 for personal widgets); decide on a
licensed stock-index source; set a Firestore TTL on `widget_cache.expires_at`. Timing: any day Monday after 7:10 AM
to Thursday; the first Sunday draft after deploy is the first issue with widgets, so read the draft check and the
admin debug view that afternoon. Rollback: remove a widget from a profile (needs agent3 redeploy) or the previous
agent3 image.

## Out of scope / follow-ups

- Stock indices and the personal stock watchlist until a licensed data source is chosen.
- Chart images (hosted PNGs); v1 uses text sparklines and tables only.
- Widgets placed inside a section, or reader-configurable edition widgets.
- Health check alerts on widgets failing several weeks in a row.
- Sending personal widgets (roadmap E wires `get_or_produce_personal` into custom sends and the preview).
