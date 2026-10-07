# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Browser Testing

After any UI change, test on all three environments:

1. **Desktop** — use `playwright`
2. **Android** — use `playwright-mobile-android` (Pixel 7 emulation)
3. **iOS** — use `playwright-mobile-ios` (iPhone 15 emulation)

Take a screenshot in each after changes. Flag any layout, overflow, or interaction issues specific to mobile viewports.

## Git Workflow

**Commit iteratively as you work — do not wait to be asked.** After each small, self-contained, verified piece of work (e.g., add CSS, update one page, create a new file, extract one helper, add one test file), commit it right away on the current feature branch. One commit per meaningful unit, never one big commit at the end of a feature. Run the relevant tests before committing; don't commit code you know is broken.

- Never commit directly to `main`; if on `main`, create a feature branch first.
- Stage only the files that belong to the unit (`git add <paths>`), never `git add -A`, so unrelated changes and secrets stay out.
- Write a short conventional-style message (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `build:`, `ci:`, `chore:`) describing the one change.
- **Never push unless explicitly asked by the user.** Committing is automatic; pushing is not.

## What This Project Does

Weekly AI newsletter pipeline. Six agents fetch ArXiv papers + news, score/summarize them with Claude, compose an HTML email, and send it via SendGrid. A separate subscription service manages subscriber preferences, and a standalone health-check agent detects and alerts on stalled or failed pipeline runs.

## Running the Pipeline

**Local run (all agents sequentially, no cloud infra required):**
```bash
export ANTHROPIC_1ST_API_KEY=sk-...
export NEWS_API_KEY=...
python orchestrator.py
```
Outputs to `data/` directory. No Firestore or Pub/Sub needed.

**Run a single agent in isolation:**
```bash
python agents/agent1a_fetch_papers.py
python agents/agent1b_fetch_news.py
# etc.
```

**Run the tests (pytest, no network/keys; CI runs them too):**
```bat
venv\Scripts\python -m pip install -r requirements-dev.txt
venv\Scripts\python -m pytest -q
```
`pytest.ini` restricts collection to `tests/`. The root-level `selection_test.py` is a manual script that makes **real Claude calls at import time** — never let pytest collect it (without `pytest.ini`, it did, silently spending API credits on every local run and failing CI, which has no key). CI (`.github/workflows/tests.yml`) runs pytest on push and PR; it only *blocks* merges if the GitHub branch ruleset requires the `pytest` check. The suite covers agent1b's graph, the shared fetcher and tracing only — green CI does not mean the rest of the app works.

**Run agent1b in the original single-pass mode (CMD):** `set AGENT1B_MODE=single_pass` then `python agents\agent1b_fetch_news.py`.

**Run the FastAPI server locally (cloud mode entrypoint):**
```bash
AGENT_NAME=agent1a uvicorn main:app --reload
```

**Docker build per agent:**
```bash
docker build --build-arg AGENT_NAME=agent1a -t ai-news-agent-1a .
```

**Build + push all services via Cloud Build:**
```bash
gcloud builds submit --config cloudbuild.yaml
```
Builds all 9 service images (agent1a/1b/2a/2b/3/4, orchestrator, agent_subscriptions, healthcheck) in parallel and pushes to Artifact Registry. Does **not** deploy to Cloud Run — that's always a separate manual `gcloud run deploy` per service (see README.md's Deployment section). `cloudbuild-partial.yaml` and `cloudbuild-subscriptions.yaml` build smaller subsets for faster iteration.

## Architecture

### Two Operating Modes

- **Local** (`USE_FIRESTORE=false`, default): `orchestrator.py` runs all agents sequentially in-process, passing data through JSON files in `data/`.
- **Cloud** (`USE_FIRESTORE=true`): Each agent is its own Cloud Run service. Events flow over Pub/Sub, pipeline state tracked in Firestore collection `pipeline_runs`.

### Agent Pipeline

```
agent1a (ArXiv papers) ──┐
                          ├──> agent2a (summarize papers) ──┐
agent1b (news fetch)  ──┘                                    ├──> agent3 (compose HTML) ──> agent4 (send)
                          └──> agent2b (summarize news)  ──┘

agent_healthcheck — independent, Cloud Scheduler-triggered (Sunday 1:15 PM draft check + 7:10 AM Monday,
not Pub/Sub). Not part of the chain above; reads pipeline_runs after the
fact and alerts on failure.
```

- **agent1a** – Fetches cs.AI/cs.LG papers from ArXiv (up to 500, last 7 days), randomly samples 35, scores with Claude using a 7-dimension 28-point rubric (`scoring_rubric.txt`), keeps top 3 (`PAPERS_IN_NEWSLETTER`). Max 5 concurrent Claude calls with exponential backoff (10s/20s/40s). PDF/API fetches route through a Squid proxy (`HTTPS_PROXY` / `HTTP_PROXY` env vars) because GCP IPs are throttled by ArXiv — both the `urllib` opener and the `arxiv` library session are patched.
- **agent1b** – Fetches the week's top 1000 Hacker News stories by points (Algolia search API — not `topstories.json`, whose front-page ranking decays so most of the week's big stories are gone by Monday) + 10 NewsAPI queries (English global, French global, Canada/Montreal), filters paywalled/non-Latin in code, then Claude language-filters (EN/FR only) and categorizes into 7 categories (`filter_tool.py` schema; language filter in 25-article batches, categorize in 100-article batches). Max 5 concurrent Claude calls. **Internally a LangGraph graph** (`agents/agent1b_graph.py`, `AGENT1B_MODE=graph` default): `fetch → prefilter → language_filter → categorize → route → (review →) finalize`. `categorize` also returns a 1–5 `confidence`; articles below `REVIEW_CONFIDENCE_THRESHOLD` (default 4; least confident first, capped at `REVIEW_MAX_ARTICLES`=30 per run) go to a `review` subgraph — a ReAct loop of `llm_call` ↔ `tool_exec` (tool `fetch_article_text`, timeout `REVIEW_FETCH_TIMEOUT`, max `REVIEW_MAX_ITERATIONS`=3 LLM calls; the model ends with a `submit_category` tool). A failed fetch / LLM error / exhausted loop degrades to the first-pass category (`review_failed`) and never fails the run. `finalize` writes the unchanged `news_filtered.json` / Firestore schema (`confidence` never leaks into it); the per-article eval audit goes to `data/agent1b_review_log.json` and, in cloud mode, Firestore `agent1b_audits/{run_id}` + a small `agent1b_review_summary` on the run doc (kept off the run doc's big fields because of the 1 MiB cap). `AGENT1B_MODE=single_pass` runs the original linear code (`collect_and_categorize_single_pass()`) — the rollback switch and the baseline for later evals. This graph is *inside* the agent: Pub/Sub, the `agent2_completions` counter, the watchdog and `_ISOLATED_RETRIES` are untouched (see `docs/decisions/0001-langgraph-inside-agents.md`). LangSmith tracing (`agents/tracing.py`) is opt-in: needs both `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY`, otherwise a no-op. The article-text fetcher is shared with agent2b via `agents/article_fetch.py` (owns the `_parse_lock` described below).
- **agent2a/2b** – Summarize papers/articles in parallel threads; use Firestore atomic counter (`agent2_completions`) to sync before triggering agent3. The agent that increments the counter to 2 publishes `content-summarized`. In cloud mode agent2b runs in a crash-isolated child process with automatic retry (see "Crash isolation + retry" below); agent2b's counter increment is idempotent (`agent2b_counted`).
- **agent3** – Runs two article-selection passes per category (all-language + English-only) to support subscriber preference variants. Claude selects the best 3-5 articles per category, weighing HN points heavily as a soft signal: each article is labelled with its points and its rank among the section's HN stories (vote counts vary by topic, so there's no fixed threshold), and NewsAPI articles are labelled `hn: unknown` and explicitly not penalized for it. Articles are shown in a neutral seeded-shuffle order so list position carries no signal; there is no code-level forced inclusion. Writes the editor's intro, then renders **4 HTML variants** keyed by `{include_french}_{include_canada}` (`0_0`, `1_0`, `0_1`, `1_1`). Saves all variants to Firestore and copies `0_0` to `public/newsletter/latest.html` for the live preview. In that same Firestore write it shrinks the run doc's article pools — `news_summaries` down to just the shipped articles, `news_filtered` down to per-category counts — because the full pools (~850 KB at ~500 articles) plus the variants (~420 KB) exceeded Firestore's 1 MiB document cap and failed the 2026-09-28 run. Both fields stay non-empty since the health check tests them for presence. (agent1b likewise writes `news_filtered.by_category` only, no duplicate flat `articles` list.)
- **agent4** – Triggered by Cloud Scheduler at 7 AM Monday (the pipeline drafts the newsletter the day before, Sunday 12 PM). In cloud mode, loads all 4 newsletter variants from Firestore, queries active subscribers, picks each subscriber's variant by preference key, substitutes `{{UNSUBSCRIBE_URL}}` and `{{PREFERENCES_URL}}` per subscriber, and sends via SendGrid. In local mode, sends a single copy to `TEST_RECIPIENT_EMAIL`. Writes its send summary (`sent`/`failed`/`failures` counts) to the *composed run's* `pipeline_runs` doc (`loaded.run_id`; before 2026-10-07 it wrongly wrote to agent4's own invocation id, so the send health check could never see it) as `agent4_send_summary` + `agent4_completed_at`, in addition to the structured JSON it already logs to stdout — the Firestore write is what makes delivery success queryable by the health check, since Cloud Logging output isn't. `_load_latest_newsletter()` picks the most recent `pipeline_runs` doc with `newsletter_composed == True` (agent4 is triggered independently by Scheduler and never receives a `run_id`) and refuses to send it — raising `StaleNewsletterError` instead — if that doc's `started_at` is more than `MAX_NEWSLETTER_AGE_HOURS` (24h) old. Without this, a pipeline that stalls anywhere before agent3 (a crash, a hang, an API failure) leaves the newest `newsletter_composed` doc pointing at an *older* successful run, and agent4 ships it with a clean-looking `send_summary` and no error anywhere — exactly what happened 2026-09-07, when agent2b's crash (see watchdog note below) meant agent4 silently re-sent the 2026-08-24 newsletter. The failure is recorded against the *stale run's own* `pipeline_runs` doc (via `StaleNewsletterError.run_id`), not agent4's invocation `run_id` — otherwise it lands on a doc with no `started_at`, which Firestore's `order_by` then silently excludes from the health check's "latest run" lookup.
- **agent_healthcheck** – See "Pipeline Failure Recording & Health Check" below.

### Pipeline Failure Recording & Health Check

Every pipeline agent's `run()` body (agent1a, agent1b, agent2a, agent2b, agent3, agent4) is wrapped in a top-level `try/except`. On an uncaught exception, a `_record_failure()` helper writes `{agent}_error` (the exception string) and `{agent}_failed_at` (UTC timestamp) to that run's `pipeline_runs/{run_id}` document before re-raising — `main.py`'s generic `_run_agent()` wrapper still catches the re-raised exception and logs a traceback to stderr as before, but now there's also a durable, queryable trace of *why* a run stalled, not just *that* it did. Without this, a failure only ever showed up in Cloud Logging, invisible to anything not actively tailing logs.

**Watchdog / hard runtime cap.** A Python `try/except` can't catch a native abort — on 2026-09-07 agent2b died with `munmap_chunk(): invalid pointer` / SIGABRT (exit 134) a few seconds into summarizing, so `_record_failure()` never ran, the agent2 counter never reached 2, `content-summarized` was never published, and agent4 shipped the *previous* week's newsletter. `main.py`'s `_run_agent()` now arms a `threading.Timer` watchdog around every agent. Deadline = whichever is sooner of (a) `MAX_RUNTIME_SECONDS` (3600) after the agent starts, or (b) 07:30 America/Toronto **if** the agent started before it (a run started after 07:30 — a manual daytime recovery — gets only the flat 1-hour cap). On expiry the watchdog best-effort writes `{agent}_error = "hard timeout — …"` + `{agent}_failed_at` to the run doc, then `os._exit(124)` — a hard exit works even if the agent thread is wedged in a C extension. `zoneinfo`/`tzdata` (added to `requirements.txt`) resolves the cutoff timezone; a fixed −5 offset is the fallback if that import fails. This bounds *hangs*; it can't help a process that has already aborted on its own (nothing left to time out) — that case is handled by crash isolation below, and otherwise caught after the fact by the health check's missing-stage detection.

**Crash isolation + retry (agent2b).** agent2b hit the same native abort again on 2026-09-28 (`free(): invalid pointer`, SIGABRT, ~3s into summarizing). Two changes:
- *Likely cause:* 20 threads running newspaper3k's `Article.parse()` concurrently — lxml/libxml2 (and, with image fetching on, Pillow) C code. `fetch_article_text()` (now in `agents/article_fetch.py`, shared with agent1b's review loop; one process-wide `_parse_lock`) sets `config.fetch_images = False` (summaries never use images) and serializes `parse()` behind `_parse_lock`; downloads stay parallel. Unconfirmed as the root cause — not reproduced.
- *Retry:* agents listed in `main.py`'s `_ISOLATED_RETRIES` (currently only `agent2b: 5`) run in a **child process** instead of the server thread. A native abort kills only the child; `_run_isolated()` re-runs it up to N more times (5s apart), all under the one watchdog deadline (which also kills the child on expiry). Child stdout/stderr are inherited, so logs are unchanged apart from `[main] agent2b attempt n/6` lines. On eventual success after a failure it deletes any `agent2b_error`/`agent2b_failed_at` an earlier attempt wrote and sets `agent2b_attempts`, so the health check doesn't flag a recovered run. If every attempt dies by signal it writes `agent2b_error = "crashed (killed by SIGABRT) on all 6 attempts"` — previously a native crash left no trace on the doc at all. Because agent2b can now run more than once per run, `increment_and_check()` is idempotent: it sets `agent2b_counted` in the same transaction and skips the increment if already set, so a retried attempt can't push `agent2_completions` to 2 before agent2a finishes.

`agents/agent_healthcheck.py` is a standalone agent (registered in `main.py`'s `AGENT_REGISTRY` as `healthcheck`) that reads that trail. It is really two checks, each triggered by its own Cloud Scheduler job rather than by Pub/Sub, selected by `POST /?check=draft|send` (`main.py` passes it to `run(run_id, check=...)`; with no `check` it picks by weekday, Sunday = draft). The **draft** check (Sunday 1:15 PM, after the noon draft; 4h staleness limit) verifies stages through agent3 plus the composed output itself (`_check_composition()`: all four variants present and non-trivial, both `{{...}}` placeholders intact, subject dated for the send day) and skips agent4's send stage and the clicks section. The **send** check (Monday 7:10 AM, after agent4's 7:00 AM send; 30h staleness limit) verifies every stage and delivery. Subjects read "draft health check" / "send health check". The pipeline now drafts Sunday 12:00 PM and agent4 sends Monday 7:00 AM; `config.newsletter_send_date()` dates a Sunday-composed issue as Monday (`STALE_AFTER_HOURS` is 30 so the Monday check accepts the ~19h-old run), so — unlike every other agent — it has no `run_id` for the pipeline run it's checking; it looks up the most recent `pipeline_runs` document itself, ordered by `started_at` descending. It then:
1. Flags a **stale run** if the latest doc's `started_at` is more than `STALE_AFTER_HOURS` (4h) old — this catches the case where the pipeline never started at all this week (e.g. the orchestrator itself failed before creating a Firestore doc), which the per-agent error fields alone wouldn't catch. `started_at` is parsed via `_parse_started_at()`, which treats a timezone-naive value as UTC — agent1a overwrites the orchestrator's aware timestamp with `datetime.now().isoformat()` (naive), and subtracting that from an aware "now" used to raise `TypeError` and crash the check before any email went out (silently, every week from 2026-08-17 to 2026-09-07).
2. Flags any of the six `{agent}_error` fields present on the doc.
3. Flags any `EXPECTED_STAGES` field missing (`scored_papers`, `news_filtered`, `paper_summaries`, `news_summaries`, `newsletter_composed`, `agent4_send_summary`).
4. Flags `agent4_send_summary` showing `sent == 0` out of a nonzero `total` (delivery ran but everything failed).

Either way, it emails exactly one report to `ALERT_EMAIL` every run — a weekly heartbeat, not just a failure alert — reusing `agent4_send.send_email()` / `_get_sendgrid_api_key()` purely as a SendGrid call; it never imports or calls anything subscriber-related, and never queries the `subscribers` collection. The subject line and body differ depending on whether anything was flagged (`"— all clear"` vs `"— problem detected"`), so sending every run doubles as confirmation that the health check itself is still running, not just that the pipeline is. `run()` wraps the whole check in a `try/except` that turns any unexpected error in the check *itself* into a "problem detected" email before re-raising — otherwise a bug in the checker (like the naive-datetime crash above) suppresses the heartbeat entirely with no signal either way.

**Known limitation, observed live:** the alert path shares SendGrid with the real newsletter send. If SendGrid itself is down or unauthorized (e.g. an expired trial/API key — exactly what happened on 2026-08-10), the health check correctly detects the failure but then can't deliver the alert about it either, since both go through the same credential. A SendGrid-independent fallback (e.g. a Cloud Monitoring log-based alert watching for a stable log marker, notifying via Monitoring's own email channel rather than app-level SendGrid calls) was scoped and then deliberately reverted — judged not worth the added complexity for now. Revisit if this actually recurs.

### Summary Judge and Click Signal (roadmap Steps 6 and 7)

Both are deployed (2026-10-06) but have not yet run on a real weekly run; see "Where things stand" below.

- **Source persistence.** agent2a/2b save the exact text each summary was written from to Firestore `summary_sources` (one doc per item, `agents/summary_sources.py`, a write failure never fails the agent). It exists so a judge can check a summary against what the summarizer saw. Needs a Firestore TTL rule on `expires_at` (collection group `summary_sources`); that one-time command has **not** been run yet.
- **Online judge.** `agents/judge.py` (verdict via tool use, `JUDGE_MODEL = claude-sonnet-5-5`; `tool_choice` must stay `auto`, because that model returns a 400 for a forced tool) and `agents/online_judge.py` (weekly sample of at most `JUDGE_MAX_ITEMS`, estimate checked against `JUDGE_MAX_USD` before any call, `judge_results` on the run doc). It runs from the healthcheck and is **report-only**: calibration against 40 human labels gave kappa 0.04 (`evals/results/judge_calibration.json`), so `JUDGE_ALERTING_ENABLED` stays False. The healthcheck service has no Anthropic key mounted on purpose, so the section says "skipped". The summarizer prompts ask for a "why it matters" sentence, so the rubric is strict on factual claims and lenient on significance sentences (`prompts/judge_prompt.txt`, `evals/README.md`).
- **Click signal.** With `CLICK_TRACKING=true`, agent4 stores `click_links/{run_id}` (the shipped articles, built from the run doc by `agents/click_links.py`) and sends each email with SendGrid click tracking on and `custom_args {run_id}` (never a subscriber id). SendGrid's signed Event Webhook posts to `POST /sendgrid/events` on agent-subscriptions: `agents/sendgrid_webhook.py` verifies the ECDSA signature on the raw body before parsing, then reduces each click event to run id, URL, timestamp and a bot flag, dropping email, IP, user agent and message ids (they must never be logged or stored). `agents/click_counts.py` increments `click_counts/{run_id}` buckets `clicks`, `early` (first 5 minutes after the send starts, mail scanners) and `bots`. The healthcheck email has an informational "Reader clicks" section (`agents/click_report.py`). Counts are rough: under 50 readers, not unique per reader, scanner noise. The rollback is `CLICK_TRACKING=false` on agent4.
- **agent4 refuses newsletters older than 24h** (`StaleNewsletterError`), so a test send is only possible shortly after a pipeline run, and a refusal writes an error onto that run's doc. Don't trigger `agent4-test` between runs.

### Incidents and Operations

Past incidents are written up in `docs/postmortems/` (index in its README). `docs/runbook.md` covers reading the health check email, re-running a stage, rollback switches and key rotation.

### Subscription Service

`agents/agent_subscriptions.py` is a standalone FastAPI app (no `run(run_id)` function). Deployed as a separate Cloud Run service. Firestore collection: `subscribers`.

**Two auth paths coexist:**

**Token-based (legacy, email links):** "Inbox is the auth" — tokens only travel inside emails. Still used for newsletter footer links (unsubscribe, preferences).
- `POST /subscribe` — double opt-in; sends confirmation email.
- `POST /request-unsubscribe` — sends unsubscribe confirmation email (always 200).
- `POST /request-preferences` — sends preferences magic link email (always 200).
- `GET /confirm?token=` — activates subscription; rotates token to 365d action token.
- `GET /unsubscribe?token=` — deactivates subscription.
- `GET/POST /preferences?token=` — read or update preferences.

**Other (no subscriber-auth model):**
- `GET /stats?token=` — admin-only (requires `ADMIN_TOKEN` as the query param, 403 otherwise); returns `{active, max}` subscriber counts.
- `GET /preview` — public, rate-limited (30/min); returns the latest newsletter HTML for the `preview.html` iframe.

**Account-based (Firebase Auth, `Authorization: Bearer <id_token>`):** Users sign in via Google or email+password through `login.html`. Firebase ID token verified in `agents/auth_middleware.py` using `firebase-admin`. No confirmation email needed — Firebase already verified the email. Creates a `users/{uid}` doc on first call.
- `GET /auth/me` — return user info + subscription status + tier.
- `POST /auth/subscribe` — subscribe instantly (email already verified; requires `email_verified: true`). Accepts `{"send_latest": bool}` body.
- `POST /auth/unsubscribe` — deactivate subscription.
- `POST /auth/send-verification-email` — rate-limited 5/min; sends a themed Firebase email-verification link for email+password accounts (no-op if already verified).
- `GET /auth/google/login?return_to=` — start Google Sign-In (see "Google Sign-In" below).
- `GET /auth/google/callback` — Google OAuth redirect target.
- `POST /auth/google/exchange` — redeem a one-time exchange code for a Firebase custom token.
- `GET /auth/preferences` — return prefs + tier.
- `POST /auth/preferences` — update prefs.
- `POST /auth/sections/refine` — **premium only**; takes `{"raw_topic": "SpaceX"}`, calls Claude Haiku, returns `{"refined_topic": "SpaceX product launches & mission updates"}`. Rate-limited 5/min.
- `GET /auth/sections` — **premium only**; returns `{default_sections, section_config}`.
- `POST /auth/sections` — **premium only**; saves user's section configuration.

**Subscriber doc fields:** `email`, `token`, `token_expires_at`, `active`, `subscribed_at`, `confirmed_at`, `prefs: {include_french, include_canada}`, `send_latest`, `latest_sent`, `uid` (Firebase UID, null for legacy subscribers). Token TTL: 48h for confirmation, 365d for action links.

**Firestore collections:** `subscribers` (existing), `users` (doc ID = Firebase UID, fields: `email`, `display_name`, `provider`, `created_at`, `tier`, `section_config`), `oauth_states` (transient, ~10min TTL, Google Sign-In CSRF state), `oauth_exchange_codes` (transient, ~60s TTL, one-time Google Sign-In handoff — see below).

### Account Tiers

Two tiers: `"free"` (default) and `"premium"`. Tier is set at login time based on the `PREMIUM_EMAILS` env var (comma-separated emails). Premium unlocks the Newsletter Sections customization UI on `preferences.html`.

**Section config schema** (stored in `users/{uid}.section_config`):
```json
{
  "enabled_sections": ["Model & Product Releases", "Industry & Business", ...],
  "custom_sections": [
    { "id": "abc123", "raw_input": "SpaceX", "refined_topic": "SpaceX product launches & mission updates" }
  ]
}
```
`enabled_sections: null` means all default sections are enabled (the default). The `DEFAULT_SECTIONS` list (canonical order) is defined in `agents/agent_subscriptions.py`.

**Future work — custom section article sourcing:** Custom sections currently store the topic preference but do not yet fetch articles. The planned approach is to use Claude's web search tool (`web_search`) in the pipeline to retrieve relevant articles for each subscriber's custom sections, then include them as additional newsletter sections. This is a pipeline-level change (agent1b or a new agent1c) tracked as a follow-up.

`main.py` conditionally mounts the subscription router when `AGENT_NAME=agent_subscriptions`. CORS `allow_headers` includes `Authorization` for the account-based routes.

### Firebase Auth Setup (one-time, manual)

In Firebase Console → Authentication → Sign-in method:
1. Enable **Google** provider
2. Enable **Email/Password** (standard, not email link)
3. Add authorized domains: `newsletter.lofeodo.com`, `latentspacemail.web.app`

`auth.js` hardcodes the Firebase project config directly in source (see the comment at the top of that file — a prior version fetched it from `/__/firebase/init.json` via a top-level `await`, replaced after causing intermittent failures on real mobile Safari). Firebase's client config isn't a secret; its security model is server-side rules, not hiding this object. `firebase serve --only hosting` is still recommended for local frontend development, since Firebase Hosting's `/__/auth/action` pages (password reset / email verification continue links) are otherwise unavailable from a plain HTTP server.

**Important — authDomain override:** `auth.js` overrides `authDomain` to `window.location.hostname` on production. This now matters only for Firebase's hosted `/__/auth/action` pages (password reset / email verification links — the email/password flow is still client-side Firebase Auth). `https://newsletter.lofeodo.com/__/auth/handler` remains registered in the Google OAuth 2.0 client's authorized redirect URIs from the old Google flow (see below); it's unused now but harmless to leave registered.

**Google Sign-In: server-side OAuth Authorization Code flow.** Google Sign-In on mobile Safari was broken across 9 client-side Firebase SDK attempts spanning two branches (popup-first, redirect-only, UA-sniffed popup-vs-redirect, every combination of `initializeAuth`/`getAuth`/persistence/`popupRedirectResolver` — see `git log` on `fix/google-signin-authdomain` and `fix/safari-auth-initialize` for the full history). The likely root cause was architectural, not a config error: Firebase's redirect flow depends on writing "a redirect is pending" state to IndexedDB immediately before navigating to `accounts.google.com` and reading it back via `getRedirectResult()` after the round trip — exactly the kind of storage Safari's Intelligent Tracking Prevention (ITP) partitions/evicts around cross-site navigation bounces.

The fix moves the entire OAuth negotiation server-side (`agents/agent_subscriptions.py`), and the client SDK never calls `signInWithPopup`/`signInWithRedirect`/`getRedirectResult` at all:
1. `login.html`/`index.html`'s "Continue with Google" is a plain `<a href="{SERVICE_BASE_URL}/auth/google/login?return_to=...">` — a real navigation, not an SDK call, so there's no client-side error path for *starting* sign-in.
2. `GET /auth/google/login` sets a CSRF `state` cookie + Firestore doc, redirects to Google's consent screen.
3. `GET /auth/google/callback` — Google redirects back here. Verifies the `state` cookie, exchanges the code for tokens server-to-server (`requests` + `google.oauth2.id_token.verify_oauth2_token`), gets-or-creates the Firebase user via Admin SDK, and redirects to `auth-callback.html` with a short-lived (60s) single-use exchange code.
4. `auth-callback.html` POSTs the exchange code to `POST /auth/google/exchange`, receives a Firebase custom token, and calls `signInWithCustomToken()`.

**Important — `create_custom_token` needs a signing-capable identity, unlike everything else this app does with Firebase Admin.** `verify_id_token` (used by every other `/auth/*` route) works fine with plain Application Default Credentials. `create_custom_token` (step 3 above, the only caller in this codebase) additionally needs to *sign* a JWT, which ADC alone can't do:
- **On Cloud Run**, the Admin SDK discovers the attached service account via the metadata server and signs remotely via the IAM Credentials API — but only if that service account has been granted `roles/iam.serviceAccountTokenCreator` **on itself**. This is not covered by `roles/editor`. One-time setup: `gcloud iam service-accounts add-iam-policy-binding <SA_EMAIL> --member="serviceAccount:<SA_EMAIL>" --role="roles/iam.serviceAccountTokenCreator"`. Without this, `/auth/google/exchange` 500s with `ValueError: Failed to determine service account`.
- **Locally**, there's no metadata server at all, so `gcloud auth application-default login` isn't sufficient on its own — `create_custom_token` fails immediately. Fix: download a service account key JSON (`gcloud iam service-accounts keys create key.json --iam-account=<SA_EMAIL>`) and set `GOOGLE_APPLICATION_CREDENTIALS` to its path before starting the backend; the key's embedded private key signs locally with no IAM call needed. Keep the key out of git (already covered by the existing `.env`/`*.env` gitignore pattern if stored alongside it, but the key file itself is not a `.env` file — gitignore it explicitly too).

The only "did sign-in succeed" signal the frontend relies on is a URL query parameter attached to an HTTP redirect — not IndexedDB, localStorage, cookies, or `window.name` — so ITP's storage-partitioning model doesn't apply to it. Every hop is either a same-tab top-level redirect or a same-origin `fetch`; there's no popup and no `window.opener` anywhere, so the original `signInWithPopup` failure mode is structurally impossible rather than merely avoided by configuration. `auth.js`'s `initializeAuth` no longer needs `popupRedirectResolver` — it was only required for `signInWithRedirect`/`signInWithPopup`/`getRedirectResult`, none of which remain in the app.

Known accepted residual risk: a narrow replay window exists between step 3's redirect and step 4's (automatic, sub-second) redemption of the exchange code. Closing it would require binding that hop to a cookie too, but the frontend and backend are different sites (`newsletter.lofeodo.com` vs `*.a.run.app`), so that cookie would necessarily be cross-site — exactly the kind of storage Safari's ITP restricts, reintroducing the fragility this rebuild exists to eliminate. Judged not worth it for v1; revisit only if abuse is observed.

**Important — Firebase SDK version:** Use `10.14.1` from the CDN only. SDK 12.x has a runtime initialization error on iPadOS Safari that silently aborts the entire `<script type="module">` block — this constraint governs the remaining email/password flow (`signInWithEmailAndPassword`, `signInWithCustomToken`, etc.) and hasn't been re-tested since it's orthogonal to the Google Sign-In rebuild.

For local development of the subscription service, Firebase Admin SDK uses Application Default Credentials: `gcloud auth application-default login`. On Cloud Run, ADC works automatically.

### Claude Tool Use Pattern

Structured outputs use tool use instead of parsing free text:
- `scoring_tool.py` – `score_paper` tool with 7-dimension schema
- `filter_tool.py` – `filter_articles` and `filter_by_language` tools. Graph mode (agent1b) uses `FILTER_TOOL_WITH_CONFIDENCE` (same tool name plus a required 1–5 `confidence` per article; the original `FILTER_TOOL` is untouched for `single_pass`), and the review loop uses `fetch_article_text` (executed by the `tool_exec` node) and `submit_category` (the model's structured final answer, handled in `llm_call`).

All Claude calls use `claude-haiku-4-5-20251001` (configured in `config.py`).

## Key Environment Variables

| Variable | Purpose |
|---|---|
| `AGENT_NAME` | Which agent to run in cloud mode |
| `ANTHROPIC_1ST_API_KEY` | Claude API key |
| `NEWS_API_KEY` | NewsAPI key |
| `SENDGRID_API_KEY` | SendGrid (or use `USE_SECRET_MANAGER=true`) |
| `USE_FIRESTORE` | Enable Firestore/Pub/Sub coordination (default: false) |
| `GCP_PROJECT_ID` | Google Cloud project |
| `TEST_RECIPIENT_EMAIL` | Local mode only: single send address for agent4 |
| `SERVICE_BASE_URL` | Public URL of the subscription service (for confirmation/unsubscribe links) |
| `FRONTEND_BASE_URL` | Public URL of the Firebase Hosting frontend (for preferences magic links). Must be `https://newsletter.lofeodo.com` — the deployed `agent4` service had this set to `https://lofeodo.com/newsletter` (wrong host, and `/newsletter` isn't a real path — Firebase Hosting serves `public/newsletter/*` at the site root), so every `{{PREFERENCES_URL}}` substituted into a sent newsletter footer was a dead link until corrected on 2026-09-11. `agent-subscriptions` had the correct value the whole time — check both services after any redeploy. |
| `HTTPS_PROXY` / `HTTP_PROXY` | Squid proxy URL for agent1a ArXiv fetches (GCP IPs are throttled) |
| `ALLOWED_ORIGINS` | CORS origins for subscription API (comma-separated; required in production) |
| `TEST_SEND_TO` | Cloud mode: skip subscriber list and send only to this address (test runs) |
| `MAILING_ADDRESS` | Physical address in email footer (CASL compliance) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Local only: path to service account JSON for Firebase Admin SDK (alternative to `gcloud auth application-default login`) |
| `PREMIUM_EMAILS` | Comma-separated emails that get `tier: "premium"` on login (e.g. `daniel.lofeodo@gmail.com`) |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth 2.0 Web client ID for server-side Google Sign-In (not secret) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth 2.0 client secret (or use `USE_SECRET_MANAGER=true`, secret name `google-oauth-client-secret`) |
| `ALERT_EMAIL` | Where `agent_healthcheck` sends a problem report; never used for subscriber-facing sends |
| `AGENT1B_MODE` | agent1b: `graph` (default, LangGraph) or `single_pass` (original code; no-redeploy rollback) |
| `REVIEW_CONFIDENCE_THRESHOLD` / `REVIEW_MAX_ARTICLES` / `REVIEW_MAX_ITERATIONS` / `REVIEW_FETCH_TIMEOUT` | agent1b review loop (defaults `4` / `30` / `3` / `10`; `REVIEW_MAX_ARTICLES=0` disables review) |
| `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | agent1b opt-in tracing; both the flag and the key are required, otherwise a no-op. Production project: `latent-spacemail-prod` |
| `CLICK_TRACKING` | agent4: `true` turns on SendGrid click tracking and tags each email with the run id (default off; set to `true` on agent4 and agent4-test on 2026-10-06) |

**Secrets on Cloud Run.** API keys are *not* plain env vars: they're mounted from Secret Manager under the same variable names, so agent code is unchanged and `gcloud run services describe` doesn't print them. Secrets: `anthropic-api-key` (agent1a/1b/2a/2b/3), `news-api-key` (agent1b), `squid-proxy-url` (agent1a/1b/2a/2b/3/4 — the URL embeds the proxy password), `langsmith-api-key` (agent1b). (The orchestrator doesn't call Claude and has no key.) Change one with `gcloud run services update SERVICE --region REGION --update-secrets VAR=secret:latest [--remove-env-vars VAR]`; use `--update-*`, never `--set-*` (which replaces everything on the service), and ship new code to an existing service with `gcloud run services update SERVICE --image IMAGE` (no prompts, no second service). Rotate by adding a new secret version. Never paste unredacted `describe` output anywhere.

## Where things stand (as of 2026-10-06)

Steps 1 to 7 of `docs/IMPROVEMENT_ROADMAP.md` are built, merged and deployed. Step 6 is ticked `[x]` (calibrated; its weekly path is still unexercised) and Step 7 is `[~]` (no real click counted yet). On 2026-10-06 all 10 Cloud Run services (agent1a/1b/2a/2b/3/4, agent4-test, orchestrator, agent-subscriptions, healthcheck) were rebuilt from `main` and updated; the SendGrid Event Webhook is created and a "Test Your Integration" call returned 200; click tracking is on for agent4 and agent4-test. `gcloud` works from PowerShell on the owner's machine, not from Git Bash (it cannot find Python there); project and region are set in the gcloud config.

**Schedule change, 2026-10-07:** the week is now split over two days. Sunday: pipeline 12:00 PM (draft), **draft health check** 1:15 PM (job `healthcheck-draft`, `/?check=draft`). Monday: agent4 send 7:00 AM, **send health check** 7:10 AM (job `healthcheck-weekly`, `/?check=send`). All America/Toronto. The point is to catch pipeline errors a day before the send, leaving time to fix and re-run. The issue is dated the Monday send date. agent3 and healthcheck were rebuilt and updated from branch `feat/sunday-draft-monday-send` (not yet merged to `main`; merge before the next build from `main`) and the three Scheduler jobs were changed; a manual test of the draft check is still pending.

**First real run on the new schedule: Sunday 2026-10-11 (pipeline 12:00 PM, draft check 1:15 PM) and Monday 2026-10-12 (agent4 7:00 AM, send check 7:10 AM).** When the owner comes back, check, in order:
0. **Draft check email (Sunday ~1:15 PM)**, subject "draft health check — all clear"; the issue and its subject are dated October 12, not 11. Then, Monday, the send check email.
1. **2a/2b logs** say "Saved N summary sources for the judge"; `summary_sources` docs exist in Firestore. Run the TTL command for that collection group if it has not been run.
2. **LangSmith** (Step 5b, still unverified): traces from agents 1a, 2a, 2b and 3 carry the `agent:` and `run:` tags, token counts and cost are filled, and the healthcheck's usage section found them.
3. **agent4 log** says "click tracking on: N links mapped for run X"; `click_links/{run}` exists; the owner's received newsletter still looks right and its links reach the articles; after the owner clicks a few, `click_counts/{run}` increments within minutes (the webhook route logs "N click events, M counted").
4. **Send check email (Monday 7:10)**: the judge section says "skipped", the clicks section says "0 clicks so far", and the drift/usage sections still render. The usage and drift sections need about three weekly runs of history before they say anything.
5. If anything is off with tracking, set `CLICK_TRACKING=false` on agent4 (`gcloud run services update agent4 --update-env-vars CLICK_TRACKING=false`), which restores the previous email exactly.

**Then:** record the real results in the roadmap's Step 6 and Step 7 entries and tick them `[x]`; the owner decides whether to mount an Anthropic key on the healthcheck so the (non-validated) judge runs weekly, and whether to improve the judge (needs a fresh labeled set); check that the signup consent wording covers click counting (CASL / Law 25); then plan Step 8 (postmortems and runbook). Other loose ends: `tests/conftest.py` does not clear the developer's Anthropic key variable, so a careless test could spend real API money again (it happened once in Step 6); the Google Cloud console restriction on the Firebase web key is the owner's to confirm; `CLAUDE.md` and the roadmap still name secrets and one real email address that the owner has asked to keep out of the README.

## Pub/Sub Topics (Cloud Mode)

`pipeline-start` → `papers-scored` + `news-filtered` → `content-summarized` → (agent3 runs) → agent4 triggered separately by Cloud Scheduler. `agent_healthcheck` is triggered by its own separate Cloud Scheduler job (Sunday 1:15 PM and Monday 7:10 AM) and is not part of this Pub/Sub chain at all.

## Prompts

All Claude prompts are in `prompts/`. Edit prompt files to change scoring behavior, summary style, or category definitions without touching Python code.
