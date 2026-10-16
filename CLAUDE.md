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

### One PR per roadmap, shipped by the agent

The owner runs many agents at once and does not want to approve or merge PRs by hand. The `protect-main` ruleset requires a PR and the `pytest` check but **0 approving reviews**, so CI is the gate.

- **One branch and one PR per roadmap**, not per step. Commit per step (above), push the branch after the first verified step, and open a **draft PR** early; keep it updated as steps land. Split a step into its own PR only if it is risky and independent of the rest.
- **Agents push their own feature branch and open, update, ready and auto-merge their own PR.** When the work is done: run the full test suite, mark the PR ready, run `gh pr merge --auto --squash --delete-branch`, and let GitHub merge when `pytest` passes. Use the `/ship` skill for this.
- **Never** push to `main`, force-push, or merge with a red check.
- **Larger work starts with `/plan`**: a planning agent asks the owner a few up-front questions, then writes `docs/plans/<name>.md`. After that the worker agent decides on its own (see below) and does not ask.

### Agent independence

Ask the owner only for things that are irreversible, outward-facing, or ambiguous in intent. For everything else, pick the sensible default, note the decision in the PR description, and carry on.

| Situation | Rule |
|---|---|
| Tests | Run relevant tests before every commit and the full suite before marking the PR ready. Never ask. |
| UI change | Do the three browser checks (above) before the last commit. Never ask. |
| Design choice with a clear default | Choose it, record it in the PR. |
| Scope growth | Log it as a follow-up in the PR; continue. Stop only if it changes the roadmap's goal. |
| Test/CI failure | Fix it. After 3 failed attempts, stop and report. |
| Prod, secrets, IAM, Firestore data, subscriber-facing sends | Do not touch. Report what is needed. |
| **Deploys** | **Agents never deploy.** Ask the owner using the deploy request format below. |

**Talking to the owner.** They are juggling several agents and forget what each one is doing. Every question, deploy request and final report starts with a plain-language recap: *what this work is, in one or two sentences, and why it matters*, before any detail. No file names, flags or jargon in the recap. Then give a recommendation, not a menu.

**Deploy request format** (use when work is merged and something needs deploying):
1. **What we're doing** — one or two plain sentences, no jargon.
2. **What changes for readers/subscribers/the pipeline** if deployed.
3. **Why deploy now (or why wait)** — your recommendation, including timing relative to the Sunday 12:00 PM pipeline and Monday 7:00 AM send. Avoid deploying in the hours before those.
4. **Risk and rollback** — how likely it is to go wrong, and the one-line way back.
5. **The exact commands** to run (services in dependency order), in a code block, then how to check it worked.

**Up-front questions** (planning agent only): at most 3, each with a one-sentence plain explanation, a recommended answer, and what changes depending on the answer. Cover scope, anything irreversible or risky, and deploy timing. If the owner is away, state the assumption you made and proceed.

**Final report** (end of every job): recap in plain words, the PR link and whether it auto-merged, what was tested, decisions made on the owner's behalf, and the deploy request if one is needed.

## What This Project Does

Weekly AI newsletter pipeline. Six agents fetch ArXiv papers + news, score/summarize them with Claude, compose an HTML email, and send it via SendGrid. A separate subscription service manages subscriber preferences, and a standalone health-check agent detects and alerts on stalled or failed pipeline runs.

## Documentation Layout

The README is deliberately short and glanceable (a portfolio page: diagram, one table per topic, generated result tables). Depth lives in `docs/`: `architecture.md`, `design-decisions.md`, `monitoring.md`, `evaluation.md`, `configuration.md`, `development.md`, `repository-structure.md`. When changing behavior, update the matching doc, not the README, unless the one-line summary there is now wrong. Result tables (in `docs/`) and the README's result charts (`docs/assets/chart-*.svg`, from `evals/make_charts.py`) are regenerated together by `python -m evals.make_readme_table`, never typed by hand. The banner (dark and light variants, picked with `<picture>`) is built by `docs/assets/build_banner.py`; the two showcase PNGs in `docs/assets/` are screenshots of the live preview and an example health check report. The README does not mention roadmap Step 8 (dropped).

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
`pytest.ini` restricts collection to `tests/`, so any script that makes real Claude calls at import time stays out of the run (an old root-level `selection_test.py` did exactly that before `pytest.ini` existed, silently spending API credits locally and failing CI, which has no key; it was deleted). CI (`.github/workflows/tests.yml`) runs pytest on push and PR; it only *blocks* merges if the GitHub branch ruleset requires the `pytest` check. The suite covers agent1b's graph, the shared fetcher and tracing only — green CI does not mean the rest of the app works.

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

- **agent1a** – Picks the week's spotlight paper from the **trending shortlist**: the 35 most-upvoted Hugging Face Daily Papers of the last 14 days (`TRENDING_LOOKBACK_DAYS`; public `huggingface.co/api/daily_papers?date=`, `agents/trending_papers.py`) that are cs.AI/cs.LG and not already in Firestore `spotlighted_papers` (doc id = arXiv id; `agents/spotlight_history.py`; local mode uses `data/spotlighted_papers.json`). Dedup happens *before* truncating to 35, so the pool refills from the next-ranked unspotlighted papers. Claude scores each on 8 dimensions (`scoring_rubric.txt`, max 33, includes `wow_factor`: would a non-specialist instantly get why it's cool/important); code adds `community_traction` (1–8, HF upvote percentile within the shortlist) for a total out of `MAX_SCORE` (41). The total is recomputed in code (`finalize_scores`), not trusted from the model. The next `PAPER_FALLBACK_CANDIDATES` (5) ranked papers go to the run doc as `paper_candidates`: if agent2a cannot summarize the top pick it tries them in order, moves the spotlight record to the paper it used, and only fails when none works; agent3 refuses an issue with no summarized paper. If PDF download fails, scoring uses the abstract. The top `PAPERS_IN_NEWSLETTER` (**1**) is recorded to `spotlighted_papers` (release runs only: a debug run does not record, so its pick cannot bar the release run from choosing it); retries of the same run don't treat their own earlier pick as seen. If HF or the arXiv lookup fails or yields <10 papers it falls back to the old path (random 35 of the last 7 days of arXiv). The newsletter links the paper to its Hugging Face page (`hf_url`), falling back to `pdf_url` for older runs. PDF/API fetches route through a Squid proxy (`HTTPS_PROXY` / `HTTP_PROXY` env vars) because GCP IPs are throttled by ArXiv — both the `urllib` opener and the `arxiv` library session are patched. Max 5 concurrent Claude calls with exponential backoff (10s/20s/40s). `AGENT1A_MODE=abstract_first` (default `full`, opt-in cost cut, `agents/paper_shortlist.py`) scores all 35 papers from their abstract, then scores the PDFs of only the top `AGENT1A_FULLTEXT_TOP_K` (8); full-text-scored papers always rank above abstract-only ones, items carry `scoring_stage`, the run doc gets `agent1a_scoring_summary`; the variable is the rollback.
- **agent1b** – Fetches the week's top 1000 Hacker News stories by points (Algolia search API — not `topstories.json`, whose front-page ranking decays so most of the week's big stories are gone by Monday) + 10 NewsAPI queries (English global, French global, Canada/Montreal), filters paywalled/non-Latin in code, then Claude language-filters (EN/FR only) and categorizes into 7 categories (`filter_tool.py` schema; language filter in 25-article batches, categorize in 100-article batches). Max 5 concurrent Claude calls. **Internally a LangGraph graph** (`agents/agent1b_graph.py`, `AGENT1B_MODE=graph` default): `fetch → prefilter → language_filter → categorize → route → (review →) finalize`. With review on, `categorize` also returns a 1–5 `confidence` (not requested when review is off, the default); articles below `REVIEW_CONFIDENCE_THRESHOLD` (default 4; least confident first, capped at `REVIEW_MAX_ARTICLES` per run; default **0 = review off** since 2026-10-15, because the review eval showed it does not help, set 30 to re-enable) go to a `review` subgraph — a ReAct loop of `llm_call` ↔ `tool_exec` (tool `fetch_article_text`, timeout `REVIEW_FETCH_TIMEOUT`, max `REVIEW_MAX_ITERATIONS`=3 LLM calls; the model ends with a `submit_category` tool). A failed fetch / LLM error / exhausted loop degrades to the first-pass category (`review_failed`) and never fails the run. `finalize` writes the unchanged `news_filtered.json` / Firestore schema (`confidence` never leaks into it); the per-article eval audit goes to `data/agent1b_review_log.json` and, in cloud mode, Firestore `agent1b_audits/{run_id}` + a small `agent1b_review_summary` on the run doc (kept off the run doc's big fields because of the 1 MiB cap). `AGENT1B_MODE=single_pass` runs the original linear code (`collect_and_categorize_single_pass()`) — the rollback switch and the baseline for later evals. This graph is *inside* the agent: Pub/Sub, the `agent2_completions` counter, the watchdog and `_ISOLATED_RETRIES` are untouched (see `docs/decisions/0001-langgraph-inside-agents.md`). LangSmith tracing (`agents/tracing.py`) is opt-in: needs both `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY`, otherwise a no-op. The article-text fetcher is shared with agent2b via `agents/article_fetch.py` (owns the `_parse_lock` described below).
- **agent2a/2b** – Summarize papers/articles in parallel threads; agent2a's paper summary is a 2-3 sentence curiosity hook (~45 words, `paper_summary_prompt.txt`), not the old two-paragraph review, so the online judge's "what it does + why it matters" rubric fits it loosely (judge is report-only). use Firestore atomic counter (`agent2_completions`) to sync before triggering agent3. The agent that increments the counter to 2 publishes `content-summarized`. `AGENT2B_MODE` (default `all`) is the cost switch: `shortlist` fetches every article's text, lets agent3's own selector (`select_with_runners_up`, both language passes) pick picks + runners-up per category from title + HN points + the first ~300 chars of text (`agents/news_shortlist.py`), and summarizes only those; a failed category is kept whole; non-shortlisted articles are omitted from `news_summaries`, and each shortlisted HN article carries `hn_rank`/`hn_of` (its rank within the whole section, from `news_shortlist.hn_context`) which agent3's `_hn_label` uses so the editor's HN signal matches the full run (it measurably changed no picks; kept for consistency at the owner's request) and a small `agent2b_shortlist_summary` lands on the run doc. Off by default, rollback is the variable. In cloud mode agent2b runs in a crash-isolated child process with automatic retry (see "Crash isolation + retry" below); agent2b's counter increment is idempotent (`agent2b_counted`).
- **agent3** – Runs two article-selection passes per category (all-language + English-only) to support subscriber preference variants. Claude selects the best 3-5 articles per category, weighing HN points heavily as a soft signal: each article is labelled with its points and its rank among the section's HN stories (vote counts vary by topic, so there's no fixed threshold), and NewsAPI articles are labelled `hn: unknown` and explicitly not penalized for it. Articles are shown in a neutral seeded-shuffle order so list position carries no signal; there is no code-level forced inclusion. Writes the editor's intro, then renders **4 HTML variants** keyed by `{include_french}_{include_canada}` (`0_0`, `1_0`, `0_1`, `1_1`). Each section's picks then go through a duplicate-removal graph (`agents/agent3_dedup_graph.py`, ADR 0002): one Haiku call over the picks, keep the highest-HN member of each duplicate group, then refill from the ranked runners-up (each checked against everything seen) until the section is back to its size or `DEDUP_MAX_ITERATIONS` (6) runs out. A dedup error degrades to the undeduped picks and never fails the run; `AGENT3_DEDUP_MODE=off` is the no-redeploy rollback. Audit: `data/agent3_dedup_log.json`, Firestore `agent3_audits/{run_id}`, and a small `agent3_dedup_summary` on the run doc. Saves all variants to Firestore (the live preview reads them from there; there is no `latest.html` file anymore). The spotlight paper renders as a distinct dark hero card (`render_paper_card`: amber left bar, HF upvote count, "Read on Hugging Face" button) placed **directly under the editor's note, above all news sections**; the TOC lists `RES` first and news numbering is unchanged. A one-line share strip (`<!-- SHARE -->` markers, outside the `SECTION` markers; pill links to `share.html`, which uses the native share sheet with copy-link/WhatsApp/X/LinkedIn/email fallbacks via `share.js`) follows the first news section; agent4's `_apply_section_config` lifts it out and re-places it after the first news section, because the reorder rewrites everything between the first and last `SECTION` marker. agent4's premium section reordering pins Research Spotlights first too (the picker in `sections.html` shows it pinned, not draggable). In that same Firestore write it shrinks the run doc's article pools — `news_summaries` down to just the shipped articles, `news_filtered` down to per-category counts — because the full pools (~850 KB at ~500 articles) plus the variants (~420 KB) exceeded Firestore's 1 MiB document cap and failed the 2026-09-28 run. Both fields stay non-empty since the health check tests them for presence. (agent1b likewise writes `news_filtered.by_category` only, no duplicate flat `articles` list.)
- **Release vs debug runs.** Every `pipeline_runs` doc has `run_kind`: `release` (the official weekly run) or `debug`. The orchestrator takes `POST /?kind=release|debug` and **defaults to `debug`**, so only the scheduled `create-newsletter` job (URI ends `?kind=release`) publishes; start debug runs by calling the orchestrator with no kind (the `create-newsletter-debug` job does this but is paused, and a paused job cannot be force-run; see `docs/runbook.md` 2b) and never force-run `create-newsletter` for debugging, that is a release. A doc with no `run_kind` (older runs) counts as release. `agents/run_kind.py` holds the shared lookup (`latest_run`, `is_release`). The public `/preview`, agent4 and the health check only follow release runs; agent4 with `TEST_SEND_TO` set (agent4-test) may also pick a debug run. To view a debug run privately, sign in as an admin (email in `ADMIN_EMAILS`, verified) and use the **Debug run** link in the top bar / the toggle on `preview.html` (`/preview.html?debug=1` opens it directly); both call `GET /auth/admin/preview?run=latest|<run_id>&variant=` with the Firebase ID token and show the HTML in the iframe via `srcdoc`.
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
4. Flags `agent4_send_summary` showing `sent == 0` out of a nonzero `total` (delivery ran but everything failed), or a failure share of `SEND_FAILURE_ALERT_RATIO` (2%) or more; fewer failures appear as an informational line.
5. Flags `agent1b_warning` (fewer than `MIN_NEWS_ARTICLES` selected, or under `MIN_ARTICLES_PER_CATEGORY` in a category; agent1b only notes it and continues). The draft check also requires the spotlight section and at least `MIN_VARIANT_ARTICLES` article links in every variant. `agent1a_warning` (spotlight history unreadable) is an informational line.

Either way, it emails exactly one report to `ALERT_EMAIL` every run — a weekly heartbeat, not just a failure alert (rendered as newsletter-styled HTML by `agents/report_html.py`, which presents the unchanged plain-text report and falls back to a plain `<pre>` if rendering ever fails) — reusing `agent4_send.send_email()` / `_get_sendgrid_api_key()` purely as a SendGrid call; it never imports or calls anything subscriber-related, and never queries the `subscribers` collection. The subject line and body differ depending on whether anything was flagged (`"— all clear"` vs `"— problem detected"`), so sending every run doubles as confirmation that the health check itself is still running, not just that the pipeline is. `run()` wraps the whole check in a `try/except` that turns any unexpected error in the check *itself* into a "problem detected" email before re-raising — otherwise a bug in the checker (like the naive-datetime crash above) suppresses the heartbeat entirely with no signal either way.

**Known limitation, observed live:** the alert path shares SendGrid with the real newsletter send. If SendGrid itself is down or unauthorized (e.g. an expired trial/API key — exactly what happened on 2026-08-10), the health check correctly detects the failure but then can't deliver the alert about it either, since both go through the same credential. A SendGrid-independent fallback (e.g. a Cloud Monitoring log-based alert watching for a stable log marker, notifying via Monitoring's own email channel rather than app-level SendGrid calls) was scoped and then deliberately reverted — judged not worth the added complexity for now. Revisit if this actually recurs.

### Summary Judge and Click Signal (roadmap Steps 6 and 7)

Both are deployed (2026-10-06) and ran on the 2026-10-12 send (clicks counted, judge report-only); see "Where things stand" below.

- **Source persistence.** agent2a/2b save the exact text each summary was written from to Firestore `summary_sources` (one doc per item, `agents/summary_sources.py`, a write failure never fails the agent). It exists so a judge can check a summary against what the summarizer saw. Needs a Firestore TTL rule on `expires_at` (collection group `summary_sources`); that one-time command has **not** been run yet.
- **Online judge.** `agents/judge.py` (verdict via tool use, `JUDGE_MODEL = claude-sonnet-5-5`; `tool_choice` must stay `auto`, because that model returns a 400 for a forced tool) and `agents/online_judge.py` (weekly sample of at most `JUDGE_MAX_ITEMS`, estimate checked against `JUDGE_MAX_USD` before any call, `judge_results` on the run doc). It runs from the healthcheck and is **report-only**: calibration against 40 human labels gave kappa 0.04 (`evals/results/judge_calibration.json`), so `JUDGE_ALERTING_ENABLED` stays False. The healthcheck service has no Anthropic key mounted on purpose, so the section says "skipped". The summarizer prompts ask for a "why it matters" sentence, so the rubric is strict on factual claims and lenient on significance sentences (`prompts/judge_prompt.txt`, `evals/README.md`).
- **Click signal.** With `CLICK_TRACKING=true`, agent4 stores `click_links/{run_id}` (the shipped articles, built from the run doc by `agents/click_links.py`) and sends each email with SendGrid click tracking on and `custom_args {run_id}` (never a subscriber id). SendGrid's signed Event Webhook posts to `POST /sendgrid/events` on agent-subscriptions: `agents/sendgrid_webhook.py` verifies the ECDSA signature on the raw body before parsing, then reduces each click event to run id, URL, timestamp and a bot flag, dropping email, IP, user agent and message ids (they must never be logged or stored). `agents/click_counts.py` increments `click_counts/{run_id}` buckets `clicks`, `early` (until 5 minutes after the send finishes: agent4 records `sent_at` on the first start and `sent_end` when done; mail scanners) and `bots`. The healthcheck email has an informational "Reader clicks" section (`agents/click_report.py`). Counts are rough: under 50 readers, not unique per reader, scanner noise. The rollback is `CLICK_TRACKING=false` on agent4.
- **Repeat runs.** `agents/stage_counter.py` counts agent2a and agent2b once each per run (`agent2a_counted` / `agent2b_counted`), so only the second finisher publishes and a repeat normally does not. The one exception: if both counted but the run doc has no `content_summarized_published` (set after the publish succeeds), a repeat of either stage publishes, so a failed publish can be recovered by re-running the stage. agent3 skips a run whose `newsletter_composed` is set unless called with `?force=1` (its first compose shrinks the article pools). agent4 writes `pipeline_runs/{run_id}/sent/{sha256(email)}` after each successful send and skips those on a re-run (not under `TEST_SEND_TO`); `agent4_send_summary.sent` includes the skipped ones, with `already_sent` giving their count, and `failures` entries carry a short hash instead of the address. A clean re-run clears an earlier `agent4_error`.
- **agent4 refuses newsletters older than 24h** (`StaleNewsletterError`), so a test send is only possible shortly after a pipeline run, and a refusal writes an error onto that run's doc. Don't trigger `agent4-test` between runs.

### Incidents and Operations

Some past incidents are written up in `docs/postmortems/` (index in its README) and `docs/runbook.md` covers reading the health check email, re-running a stage, rollback switches and key rotation. These came from roadmap Step 8, which was **deprecated and dropped as a step**: do not plan further postmortem or runbook work, and do not reference them from the README. The existing files are left in place as-is.

### Subscription Service

`agents/agent_subscriptions.py` is a standalone FastAPI app (no `run(run_id)` function). Deployed as a separate Cloud Run service. Firestore collection: `subscribers`.

**Two auth paths coexist:**

**Token-based (legacy, email links):** "Inbox is the auth" — tokens only travel inside emails. Still used for newsletter footer links (unsubscribe, preferences).
- `POST /subscribe` — double opt-in; sends confirmation email.
- `POST /request-unsubscribe` — sends unsubscribe confirmation email (always 200).
- `POST /request-preferences` — sends preferences magic link email (always 200).
- `GET /confirm?token=` — shows a page with a Confirm button (changes nothing). `POST /confirm?token=` (the button) activates the subscription and keeps the link's token, extending its expiry to 365d; repeating it just shows the success page.
- `GET /unsubscribe?token=` — shows a page with an Unsubscribe button (changes nothing). `POST /unsubscribe?token=` (the button) deactivates the subscription.
- `GET/POST /preferences?token=` — read or update preferences.

**Other (no subscriber-auth model):**
- `GET /spots` — public, rate-limited, 60 s cache; returns `{active, max}` for the home page counter.
- `GET /stats` — owner-only; 404 while `ADMIN_TOKEN` is unset, otherwise needs it as `Authorization: Bearer` (or the `token` query param); same body as `/spots`.
- Pacing (`agents/mail_throttle.py`): `/subscribe`, `/request-unsubscribe` and `/request-preferences` send at most one mail per address per `MAIL_COOLDOWN_SECONDS` and `MAIL_MAX_PER_ADDRESS_DAILY` per day, within a `MAIL_DAILY_CEILING`. The only subscriber limit is `MAX_SUBSCRIBERS` (default 40000, shown on the home page counter via `/spots`). A paced request gets the normal response. Never-confirmed subscriber docs carry `expires_at` (7 days, for a Firestore TTL rule; removed on confirm). The rate limiter keys on the last `X-Forwarded-For` entry on Cloud Run. The three mail routes each take at least `RESPONSE_FLOOR_SECONDS` (default 2.0) whatever the address, slept inside the request because the service has no CPU between requests; outgoing mail calls time out after `SENDGRID_TIMEOUT_SECONDS` (8). `/auth/*` handlers are plain `def` so blocking calls run in the worker pool.
- `GET /issues/latest` — public, rate-limited (30/min); returns the latest *release* run's newsletter HTML for the `issues.html` iframe. The response carries a content policy (`PREVIEW_CSP`: no script, https images, inline styles only) and the iframe is sandboxed to popups only, so article links open in a new tab; the admin `srcdoc` copy gets the same policy through a meta tag. Admins use `GET /auth/admin/preview` (Bearer token, `ADMIN_EMAILS`) for any run, debug ones included; `/auth/me` returns `is_admin`.
- `GET /issues`, `GET /issues/{run_id}` — public, rate-limited (30/min); the issue switcher on `issues.html` (`/preview.html` 301-redirects there). Lists/serves composed runs whose `run_kind` is *explicitly* `release` (`run_kind.is_archived_release`; a missing flag does not count, unlike `is_release`), with the same rendering and `PREVIEW_CSP` as `/preview`; anything else is a 404. Old Monday-morning issues are flagged by `tools/backfill_release_flag.py` (dry run unless `--apply`). Plan: `docs/plans/newsletter-archive.md`.

**Account-based (Firebase Auth, `Authorization: Bearer <id_token>`):** Users sign in with Google through `login.html` (new email+password accounts were removed 2026-10-07; accounts that already exist still work while signed in, and email-only people use the emailed token links, which work for every subscriber since subscribers are keyed by email). Firebase ID token verified in `agents/auth_middleware.py` using `firebase-admin`. No confirmation email needed — Firebase already verified the email. Creates a `users/{uid}` doc on first call.
- `GET /auth/me` — return user info + subscription status + tier.
- `POST /auth/subscribe` — subscribe instantly (email already verified; requires `email_verified: true`). Accepts `{"send_latest": bool}` body; that mail is the newest release issue agent4 has already sent (`run_kind.latest_run(..., sent_only=True)`), so never a debug run or a draft still waiting for Monday.
- `POST /auth/unsubscribe` — deactivate subscription.
- `GET /auth/google/login?return_to=` — start Google Sign-In (see "Google Sign-In" below).
- `GET /auth/google/callback` — Google OAuth redirect target.
- `POST /auth/google/exchange` — redeem a one-time exchange code for a Firebase custom token.
- `GET /auth/preferences` — return prefs + tier.
- `POST /auth/preferences` — update prefs.
- `POST /auth/sections/refine` — **premium only**; takes `{"raw_topic": "SpaceX"}`, calls Claude Haiku, returns `{"refined_topic": "SpaceX product launches & mission updates"}`. Rate-limited 5/min.
- `GET /auth/sections` — **premium only**; returns `{default_sections, section_config}`.
- `POST /auth/sections` — **premium only**; saves user's section configuration.

**Subscriber doc fields:** `email`, `token`, `token_expires_at`, `active`, `subscribed_at`, `confirmed_at`, `prefs: {include_french, include_canada}`, `send_latest`, `latest_sent`, `uid` (Firebase UID, null for legacy subscribers). Token TTL: 48h for confirmation, 365d for action links; the expiry date only applies to pending sign-ups and `/confirm`, so unsubscribe and preferences links keep working for active subscribers. Signing in never subscribes by itself (only the Subscribe button / sign-up form does), and `POST /auth/subscribe` on an existing doc issues a fresh token.

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
2. **Email/Password** is no longer used for new accounts; leave it enabled so existing password users' sessions keep refreshing
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
| `ADMIN_EMAILS` | Comma-separated verified emails that may view debug runs (`/auth/admin/issues`; the Debug run button); set on agent-subscriptions |
| `PREMIUM_EMAILS` | Comma-separated emails that get `tier: "premium"` on login (e.g. `owner@example.com`) |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth 2.0 Web client ID for server-side Google Sign-In (not secret) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth 2.0 client secret (or use `USE_SECRET_MANAGER=true`, secret name `google-oauth-client-secret`) |
| `ALERT_EMAIL` | Where `agent_healthcheck` sends a problem report; never used for subscriber-facing sends |
| `AGENT1B_MODE` | agent1b: `graph` (default, LangGraph) or `single_pass` (original code; no-redeploy rollback) |
| `REVIEW_CONFIDENCE_THRESHOLD` / `REVIEW_MAX_ARTICLES` / `REVIEW_MAX_ITERATIONS` / `REVIEW_FETCH_TIMEOUT` | agent1b review loop (defaults `4` / `0` / `3` / `10`; `REVIEW_MAX_ARTICLES=0` = review off, the default) |
| `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | agent1b opt-in tracing; both the flag and the key are required, otherwise a no-op. Production project: `latent-spacemail-prod` |
| `AGENT1A_MODE` / `AGENT1A_FULLTEXT_TOP_K` | agent1a: `full` (default) or `abstract_first` (abstract pass, then full text for the top K, default 8; opt-in cost cut, no-redeploy switch) |
| `AGENT2B_MODE` | agent2b: `all` (default, summarize everything) or `shortlist` (summarize only a first-pass selection; opt-in cost cut, no-redeploy switch) |
| `AGENT3_DEDUP_MODE` / `DEDUP_MAX_ITERATIONS` | agent3 duplicate removal: `graph` (default) or `off` (rollback); max fallback checks per section and pass (default 6) |
| `CLICK_TRACKING` | agent4: `true` turns on SendGrid click tracking and tags each email with the run id (default off; set to `true` on agent4 and agent4-test on 2026-10-06) |

**Secrets on Cloud Run.** API keys are *not* plain env vars: they're mounted from Secret Manager under the same variable names, so agent code is unchanged and `gcloud run services describe` doesn't print them. Secrets: `anthropic-api-key` (agent1a/1b/2a/2b/3), `news-api-key` (agent1b), `squid-proxy-url` (agent1a/1b/2a/2b/3/4 — the URL embeds the proxy password), `langsmith-api-key` (agent1b). (The orchestrator doesn't call Claude and has no key.) Change one with `gcloud run services update SERVICE --region REGION --update-secrets VAR=secret:latest [--remove-env-vars VAR]`; use `--update-*`, never `--set-*` (which replaces everything on the service), and ship new code to an existing service with `gcloud run services update SERVICE --image IMAGE` (no prompts, no second service). Rotate by adding a new secret version. Never paste unredacted `describe` output anywhere.

## Where things stand (as of 2026-10-14)

The improvement roadmap is finished and archived at `docs/history/IMPROVEMENT_ROADMAP.md` (Step 8 was deprecated and never done; Optional A, a privacy review, and Optional B, an agent2b verify loop, are open). All 10 Cloud Run services were rebuilt from `main` (PR #100) and updated on 2026-10-13, and Firestore TTL rules on `expires_at` are active for `subscribers`, `mail_throttle`, `mail_budget`, `summary_sources`, `sent`, `oauth_states` and `oauth_exchange_codes`. `gcloud` works from PowerShell on the owner's machine, not from Git Bash; project and region are set in the gcloud config.

**First run on the Sunday/Monday schedule: 2026-10-11 (pipeline 12:00 PM, draft check 1:15 PM) and 2026-10-12 (agent4 7:00 AM, send check 7:10 AM).** Checked 2026-10-14: all Scheduler jobs fired on time; both health checks reported "all clear"; the issue ("October 12, 2026") went to 15 of 15 subscribers with 0 failures and no `*_error` field on the run doc; agent2a/2b logged "Saved N summary sources" and `summary_sources` docs exist; agent4 logged "click tracking on: 31 links mapped", `click_links/{run}` exists and `click_counts/{run}` has click buckets. That send ran on the code deployed before 2026-10-13, so `click_links` has `sent_at` but not yet `sent_end` / `send_complete`; those first appear on the 2026-10-19 send. Not checked from here: LangSmith traces carrying the `agent:` / `run:` tags and token counts, the judge and clicks wording in the send check email, and the owner's received newsletter links.

**Merged-but-not-deployed (as of 2026-10-14, branch `docs/release-notes`):** the preview-page script fix, the stage-counter publish recovery, the sent-issue lookup for the latest-edition mail, NewsAPI key in a header, hosting response headers, ignore-file additions. Deploy agent1b, agent2a, agent2b, agent-subscriptions, then hosting; after the hosting deploy check the new headers with `curl -I` and that the admin Debug run toggle on `preview.html` works.

**Next:** on the 2026-10-18/19 run confirm `sent_end` is written, the draft check passes the new composition counts, and agent2a needed no paper fallback. Then add the real judge and click results to `docs/monitoring.md` and the README's Results section (the owner adds them); the owner decides whether to mount an Anthropic key on the healthcheck so the (non-validated) judge runs weekly, and whether to check that the signup consent wording covers click counting (CASL / Law 25). If tracking misbehaves, set `CLICK_TRACKING=false` on agent4 (`gcloud run services update agent4 --update-env-vars CLICK_TRACKING=false`), which restores the previous email exactly. Other loose ends: `tests/conftest.py` does not clear the developer's Anthropic key variable, so a careless test could spend real API money; the archived roadmap still names secrets that the owner has asked to keep out of the README.

## Pub/Sub Topics (Cloud Mode)

`pipeline-start` → `papers-scored` + `news-filtered` → `content-summarized` → (agent3 runs) → agent4 triggered separately by Cloud Scheduler. `agent_healthcheck` is triggered by its own separate Cloud Scheduler job (Sunday 1:15 PM and Monday 7:10 AM) and is not part of this Pub/Sub chain at all.

## Prompts

All Claude prompts are in `prompts/`. Edit prompt files to change scoring behavior, summary style, or category definitions without touching Python code.
