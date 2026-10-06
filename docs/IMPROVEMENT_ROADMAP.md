# Latent SpaceMail improvement roadmap (TEMPORARY)

Temporary working doc. It tracks a series of improvements to this repo, one step at a time. It is deleted or archived in the final step. The repo is public, so everything committed is public: keep docs honest and polished, and never commit secrets, subscriber data, or real email addresses.

Purpose: make Latent SpaceMail defensible in technical interviews for LLM systems roles by adding evaluation, monitoring, security testing and operational write-ups on top of the existing LangGraph work. Every claim the repo makes must be backed by a number produced by a script.

## Locked decisions

| Area | Decision |
|---|---|
| Cloud orchestration | Do not touch the Pub/Sub chain, the `agent2_completions` counter, the watchdog, `_ISOLATED_RETRIES`, or Cloud Run service definitions unless a step's plan explicitly justifies it and I approve. |
| Schemas | Existing data/*.json and Firestore document shapes stay backward compatible. New fields are additive only. |
| Models | Use the existing model config. Never hardcode a model name. |
| Labels | I write all gold labels. You generate labeling templates and never fill or edit label columns. Labeling is the critical path, so templates are produced early (Step 2). |
| Judge model | The LLM judge must be a different model family from the model being judged, or at minimum a stronger model. Validate it against my human labels with Cohen's kappa, and document its limits. |
| Statistics | Report n with every rate and a Wilson 95 percent interval. Report per-item paired wins and losses when comparing variants. Do not claim an improvement that sits inside the noise. Say so when samples are small. |
| Numbers | Every number in docs comes from a generated results file (`evals/results/*.json`). Never hand-type or invent a metric. If a result is unflattering, report it plainly. |
| Cost | Before any run that calls a paid API at scale, state the estimated cost and wait for my OK if it exceeds 2 USD. |
| Data in the public repo | Do not commit third-party article full text. Commit URLs, ids, titles and short snippets, plus a script that re-fetches. Full-text fixtures stay gitignored. |
| Privacy | Per-article feedback is aggregate counts only. No subscriber PII in LLM calls, traces, fixtures or logs. |
| Tests | Default tests use stubbed clients and no network or keys, so they run in CI. Anything that calls a real API is a separate, on-demand command. |
| Shell | I use Windows CMD (WSL also exists). Give commands in CMD syntax (`set VAR=value`, backslash paths). |

## Step sequence

- [x] Step 0: Roadmap doc (this file)
- [x] Step 1: Cleanup and repo hygiene
- [x] Step 2: Eval foundations and labeling templates
- [x] Step 3: Review-step evaluation (single_pass vs graph)
- [x] Step 4: Prompt-injection tests
- [x] Step 5: Drift monitoring
- [x] Step 5b: Token and cost monitoring (LangSmith, weekly summary and drift alert)
- [x] Step 6: Online judge (calibrated weekly scoring and alerting). Built, calibrated and recorded; the judge did not validate against the owner's labels (kappa 0.04), so it ships report-only. Deployed 2026-10-06; its weekly path is first exercised on the 2026-10-12 run
- [~] Step 7: Click-through signal (SendGrid). Built, tested and deployed 2026-10-06; no real click has been counted yet, first tracked send is 2026-10-12 (see "Completed steps")
- [~] Step 8: Postmortems and runbook. Written and tested on branch `docs/postmortems-runbook`; open: the owner's facts for the `OWNER:` markers (see "Completed steps")
- [ ] Optional A: Model card and privacy review (Law 25 / GDPR)
- [ ] Optional B: Agent 2b verify loop (generate, verify, retry or fall back)
- [ ] Step 9: Results, README, CLAUDE.md, retire this doc

## Workflow rules

- One step at a time. Do not plan future steps in detail up front.
- To start a step: I say so explicitly, then you enter plan mode, write a detailed plan for that step only, append it under "Step detail" below, and wait for my approval before writing code.
- Each step gets its own branch, named `feat/<step-name>` (or `docs/`, `chore/`, `test/` as fitting).
- Commit freely on the step branch in small, continual commits. Do not push or open a PR until I say the step is ready. I merge PRs myself.
- Update this file's checklist and Step detail as work progresses, not only at step boundaries.
- The step's own PR must include its roadmap update: before declaring the step ready, tick its checklist box and describe it thoroughly under "Completed steps" (what was built, files touched, key decisions, deviations from the plan, how to run it, results with caveats), all on the step branch. Never leave this for an after-the-fact edit once the PR is merged.
- Tell me explicitly when a step is fully complete before starting the next step's plan.
- If the code contradicts anything in this document, say so and propose a fix to the doc before proceeding.

## Steps at a glance

### Step 1: Cleanup and repo hygiene
Goal: remove noise a reviewer would notice, before building more.
Scope: delete the zero-byte tracked `[internal]` file; fill or delete the empty `tests/test_fetch.py`; pin the unpinned tail of requirements.txt, verified under Python 3.11 (Docker base) with a Docker build; propose, and ask me about, adding a Docker build check to CI. Keep `selection_test.py` out of pytest collection as it is now.
Needs from me: approval on pinning and the CI addition.

### Step 2: Eval foundations and labeling templates
Goal: shared infrastructure for every later eval, and early labeling templates so I can label while other steps are built.
Scope: an `evals/` package with conventions for fixtures, a results-file schema, a cost estimator and guard, a Wilson interval helper, and a kappa helper (all unit tested). A script that snapshots a real run into frozen inputs, respecting the data-in-public-repo rule. Labeling templates generated from real data: about 100 articles for Agent 1b (title, snippet, first-pass category, confidence, blank gold category) and about 40 generated summaries from agents 2a and 2b with their source text (blank supported or unsupported column). Determine in the plan where real data can be pulled from (recent Firestore runs, audit logs, or a fresh local run) and what it would cost.
Needs from me: label both templates. This is the long pole, so start as soon as the templates exist.

### Step 3: Review-step evaluation
Goal: find out whether the LangGraph review loop actually helps.
Scope: run `AGENT1B_MODE=single_pass` and `graph` on identical frozen inputs. Metrics: category accuracy overall and on the low-confidence subset; calibration (accuracy per confidence bucket); share routed to review; average tool calls; extra tokens, cost and latency; paired wins and losses. If verbalized confidence does not predict errors, say so and evaluate one alternative routing signal. Generated results table; README reads from it.
Depends on: Step 2 and my 100 labels.

### Step 4: Prompt-injection tests
Goal: replace "we added a guard" with measured evidence.
Scope: (a) deterministic tests in CI: the URL sanitizer rejects non-http(s) schemes, untrusted content is always wrapped in its tags, the guard text is present in every system prompt that handles external content, and LLM output and article titles are HTML-escaped where newsletters are rendered. (b) An on-demand behavioral harness that feeds poisoned fixtures (instruction override, forced category, prompt leak attempt, HTML or URL payload) through agents 1b (including the review loop and fetched text), 2a, 2b and 3, and reports pass rate per attack type and agent. Fix real weaknesses found, and record both the failures and the fixes.
Depends on: Step 2 (harness conventions).

### Step 5: Drift monitoring
Goal: detect when the pipeline's behavior shifts, without needing labels.
Scope: persist per-run distributions (Agent 1a rubric score distribution, Agent 1b confidence and category mix, review rate) in Firestore, ideally backfilled from existing run history. Add a drift test of this week against the prior 4 weeks (KS for scores, a suitable test for category mix). Acknowledge the small samples and weekly cadence, and choose thresholds that avoid false alarms. Surface results in the healthcheck's weekly email. A monitoring failure must never suppress the healthcheck heartbeat.
Depends on: nothing from earlier steps except conventions.

### Step 5b: Token and cost monitoring
Goal: know what each weekly run costs, and notice when token use or cost drifts.
Scope: use LangSmith as the source of per-run token counts and cost across the pipeline's Claude calls. Today LangSmith tracing is opt-in and covers agent1b only (project `latent-spacemail-prod`), and agent1b already stores a `token_usage` summary on the run doc, so the plan must decide whether to extend tracing to agents 1a, 2a, 2b, 3 and the subscriptions refine endpoint, or to persist per-agent usage in Firestore and use LangSmith for inspection, with reasoning. Add a token and cost summary (per agent and total, this week vs the prior weeks) to the healthcheck email, and a drift test on tokens and cost that reuses Step 5's drift helpers, thresholds approach and "a monitoring failure must never suppress the heartbeat" rule. Cost uses `evals/cost.py`'s price table, so the price source and its staleness are stated in the plan. Aggregate counts only: no article text, no subscriber data in traces or logs. If the healthcheck must call the LangSmith API, that adds a secret to the healthcheck service, which the plan must call out and get approved.
Depends on: Step 5.

### Step 6: Online judge
Goal: score the content readers actually see, weekly, with a validated judge.
Scope: judge target is faithfulness of generated summaries to their source text (agents 2a and 2b), because it is checkable and user-facing. Calibrate the judge on my 40 labels with kappa, then score a bounded weekly sample of production output, store results, and alert on a quality drop. Include a hard sample-size and cost cap. Choose where it runs (healthcheck extension or its own Scheduler-triggered job) in the plan, with reasoning.
Depends on: Steps 2 and 5, and my 40 summary labels.

### Step 7: Click-through signal
Goal: an implicit quality signal from real readers.
Scope: investigate what SendGrid can provide (link tracking, event webhook, stats API) and whether per-article aggregate counts are feasible without per-subscriber tracking. If feasible, capture aggregate clicks per article and category and add them to the weekly monitoring summary. If not feasible within the privacy rule, document why and drop this step.
Depends on: Step 5.

### Step 8: Postmortems and runbook
Goal: show operational maturity with real incidents.
Scope: `docs/postmortems/` with a consistent template (summary, impact, timeline, root cause with confidence stated, detection, fixes, lessons, follow-ups). Start with the 2026-09-07 stale newsletter, then the 2026-09-28 Firestore 1 MiB failure, then the FRONTEND_BASE_URL dead links if they merit it. Source facts only from CLAUDE.md, git history and code; ask me for anything missing (times, impact). Keep the honest note that the SIGABRT root cause is unconfirmed. Add `docs/runbook.md` (how to diagnose healthcheck alerts, rerun a pipeline stage, roll back with `AGENT1B_MODE`, rotate keys). Where feasible, add stub-based regression tests for each incident fix.

### Step 9: Results, README, CLAUDE.md, retire this doc
Goal: make the repo tell its story.
Scope: README section with the eval and monitoring results (generated tables first), architecture notes updated, CLAUDE.md updated, and a decision from me on deleting or archiving this file.

### Optional A: Model card and privacy review
A short model card and a data-flow and retention review (what is stored, for how long, consent and unsubscribe flow, what leaves to LLM providers). Only if time allows.

### Optional B: Agent 2b verify loop
Fetch, summarize, verify against the source, retry once, else fall back to an extractive snippet. Built as a LangGraph subgraph, evaluated with the labeled summaries and the Step 6 judge. Only after Step 6.

## Step detail

(Plans are appended here, one per step, when that step starts.)

### Step 1 plan (branch `chore/repo-hygiene`, approved)

Code vs. roadmap: all claims held, plus these differences.
- `.dockerignore` also carried a stray `[internal]` first line (removed).
- The local venv is Python 3.14 but Docker and CI use 3.11, so the new pins come from a `python:3.11-slim` resolve, not the venv.
- The dockerfile's separate `pip install fastapi uvicorn` was redundant with requirements.txt (removed).
- `tests/test_fetch.py` was 3 bytes, untouched since the initial commit, and unreferenced (deleted; `tests/test_article_fetch.py` covers the fetcher).

Work done: removed `[internal]` and the `.dockerignore` line; deleted `tests/test_fetch.py`; pinned the 8 unpinned packages plus their transitive deps in requirements.txt and pytest (with its deps) in requirements-dev.txt; added a `docker-build` job (build only, no push) to `.github/workflows/tests.yml`.

Verification: 25 tests pass locally (3.14) and in the 3.11 image; `pip check` is clean; imports of `main`, the subscription, healthcheck and send agents succeed in the image (imports only, no cloud calls); images build for `agent1b` and `agent_subscriptions`.

To repin later: install `requirements.txt` plus pytest in a `python:3.11-slim` container, run `pip freeze`, and update the files from that output.

The `docker-build` job only blocks merges if the branch ruleset requires it.

### Step 2 plan (branch `test/eval-foundations`, approved)

Code vs. roadmap: summaries have no stored source text (agent2b fetches live, capped at 1500 words; agent2a uses PDF text capped at 5000 words), so the template generator re-fetches it; `agent1b_review_log.json` has no titles (joined to `news_filtered.json` by url) and only covers articles that survived selection, so category accuracy is measurable but selection recall is not; only 3 papers are summarized per run; no stats or cost helpers existed.

Decisions: use `scipy` (Wilson) and `scikit-learn` (kappa) as dev-only dependencies rather than hand-rolled maths; article labels from the 2026-09-30 local run (agent3 prunes Firestore's `news_filtered` and the audit docs have no titles); news summaries from the existing local `data/news_summaries.json` (read-only, dated 2026-06-13) and papers from Firestore `pipeline_runs`; no LLM calls, so the run cost 0 USD.

### Step 3 plan (branch `feat/review-eval`, approved)

Code vs. roadmap: `build_graph` and `collect_and_categorize_single_pass()` start from a live fetch, but the seam for frozen inputs already exists: `agent1b_fetch_news.filter_and_categorize(articles, client, with_confidence)` and the injectable `build_review_graph(client, fetcher, cfg)`. The runner calls those directly on `evals/fixtures/articles_frozen.json`; no production code changes.

Design:
- `evals/review_eval.py` (pure, stub-tested): gold loading and join; Wilson-interval accuracy overall and per stratum for single_pass, graph first pass and graph final; calibration per confidence bucket with an explicit verdict if confidence does not predict errors; share routed to review, tool calls, extra tokens, cost and latency; paired wins and losses; one alternative routing signal only if confidence proves uninformative.
- `evals/run_review_eval.py` (on demand, real API, `--dry-run`, guarded by `CostGuard`): categorizes all 500 frozen articles in production-sized batches so batch context matches, scores the 100 labeled ones, and runs the review loop for labeled articles below the confidence threshold with the production cap lifted. Categorization is re-run fresh so accuracy is not circular with the first-pass column the labeler saw. Fetched text is cached under gitignored `evals/fixtures/private/` so reruns are reproducible despite link rot.
- `evals/make_readme_table.py`: regenerates a marked README block from the latest results file.
- All default tests use stubs and need no network.

Verification: pytest green; `--dry-run` prints the cost estimate; the paid run only after the owner approves the estimate. Intervals are wide at n=100 (n=40 low-confidence) and results are reported plainly, including a null result.

### Step 4 plan (branch `test/prompt-injection`, approved)

Code vs. roadmap: every Claude call except the subscriptions refine endpoint already set a `system=` guard, but the wording was duplicated inline in 7 places (only agent3 had a constant) and nothing was tested; 1a, 2a and 2b wrap content in plain labels, not tags; `/auth/sections/refine` had no system prompt or tags; no test covered `_safe_url`, any `system=` string or agent3 rendering. Weaknesses found by reading the code: `_safe_url` did not escape quotes (attribute breakout in `href`); closing tags inside titles, descriptions or fetched text were not neutralised; the review loop fetched model-chosen URLs with no private-address check; `filter_batch` trusted `category` and `index` from the tool input; `sections.html` put `refined_topic` into `innerHTML` and `POST /auth/sections` stored any client string; `{{UNSUBSCRIBE_URL}}` literals in article text would be substituted by agent4.

Design: (A) deterministic stub tests in CI, written first with `xfail(strict=True)` for each weakness so a fix flips them; (B) an on-demand harness (`evals/run_injection_eval.py`) driving the real agent functions with hand-written poisoned fixtures, each case run with and without the injection (attack and control arms), judged deterministically with no LLM judge; (C) run the harness for a baseline before touching production code, fix, re-run.

### Step 5 plan (branch `feat/drift-monitoring`, approved)

Code vs. roadmap: agent1a persists only the top 3 papers (survivorship-biased, no history), so agent1a score drift is dropped by owner decision. `agent1b_review_summary` has no confidence histogram or category mix, but `agent1b_audits/{run_id}` does (graph-mode runs only, not pruned) and is the backfill source; agent3 prunes `news_filtered` to per-category counts. The healthcheck read only the latest run doc and had no tests. scipy and numpy were dev-only, so they are added to `requirements.txt` (owner decision), which changes the runtime image.

Design: (1) additive `confidence_hist` and `category_counts` in `agent1b_review_summary`, computed in `_write_review_audit`; (2) pure `agents/drift.py` (KS on confidence, chi-square or seeded permutation on category mix, Fisher exact on review rate); a flag needs p < 0.01 and an effect-size floor, and fewer than 3 prior runs gives `insufficient_history`; (3) `agents/drift_history.py` loads the latest runs, falling back to audit docs for older runs; (4) drift is its own section in the healthcheck email, built inside its own `try/except`, and never changes the all clear or problem status or suppresses the heartbeat; (5) on-demand `evals/backfill_drift_summary.py` (`--dry-run` default, no LLM calls). Thresholds live in `config.py`. Small weekly samples make this a tripwire, not a guarantee.

### Step 5b plan (branch `feat/cost-monitoring`, approved)

Code vs. roadmap: only agent1b graph mode read `response.usage` and only agent1b was traced in LangSmith (opt-in, `agents/tracing.py`, set up 2026-09-30; the `langsmith-api-key` secret and `LANGSMITH_*` vars already exist on the agent1b service). LangSmith's free Developer plan has 5k base traces a month, shared across the account, and 14-day retention, so it cannot hold the 4 prior weeks a drift baseline needs. The owner's idea, adopted: have the healthcheck copy each run's LangSmith totals into Firestore every week (`pipeline_runs/{run_id}.llm_usage`), so history accumulates while LangSmith only keeps recent traces. The owner also chose to show both LangSmith's cost and a list-price estimate from our own table, and to flag drift in the email without changing the pipeline status (as in Step 5).

### Step 6 plan (branch `feat/online-judge`, approved)

Code vs. roadmap: summaries carry no source text (agent2b fetches live, agent2a reads the PDF) and the healthcheck has no ArXiv proxy, so the judge cannot rebuild a source later; agents 2a and 2b now persist the exact text they summarized to a new Firestore collection `summary_sources` (additive, one doc per item, TTL 21 days). agent3 prunes `news_summaries` to the shipped articles, so the weekly sample is drawn from what readers saw. Owner decisions: judge `claude-sonnet-5-5` (stronger, same family; bias documented as a limit), runs inside the healthcheck, owner labels the 40 summaries first.

Design: pure `agents/judge.py` (tool-use verdict, guard text, tag neutralising); `agents/online_judge.py` (sample of at most `JUDGE_MAX_ITEMS`=12, papers first then a seeded news sample stratified by `used_fallback`; cost estimate checked against `JUDGE_MAX_USD`=0.25 before any call; results on the run doc as `judge_results`, counts and verdicts only; idempotent); `drift.evaluate_judge` (Fisher exact on the unsupported rate, p < 0.01 and a 15-point rise); a healthcheck section that is report-only until `JUDGE_ALERTING_ENABLED` is flipped after calibration; `evals/run_judge_calibration.py` (kappa against the 40 labels, on demand).

### Step 7 plan (branch `feat/click-signal`, approved)

Code vs. roadmap: SendGrid's Event Webhook click events carry the original target `url` and any `custom_args`, so per-article counts need no rewriting of our HTML; but every event also carries the recipient's email, IP and user agent, and the Stats API only gives totals. The owner first chose a first-party redirect for privacy, then reversed after review: the webhook handler that aggregates in memory and drops the personal fields satisfies the "aggregate counts only" rule, uses a tool the owner already pays for, and avoids making every newsletter link depend on our own service. Checked by the owner in the console: Click Tracking and Event Webhooks with Signature Verification are available on the Essentials 50k plan (2 webhooks allowed, this uses 1); link branding was skipped as too complex. Audience is under 50 active subscribers, so this is a rough signal (counts and n only, no tests).

Design: agent4 builds a link map from the run doc (shipped articles and papers) and stores it as `click_links/{run_id}`, then sends each email with SendGrid click tracking on and `custom_args {run_id}` (behind `CLICK_TRACKING`, failing open); a signed `POST /sendgrid/events` route verifies the ECDSA signature before parsing, reduces events to `{run_id, url, timestamp, bot}`, and increments `click_counts/{run_id}`; the healthcheck email gets an informational click section.

### Step 8 plan (branch `docs/postmortems-runbook`, approved)

Code vs. roadmap: the 2026-09-07 incident is a chain (agent 2b native abort, then no freshness check in agent 4, then a healthcheck that had been crashing silently since 2026-08-17), fixed across three dates; 2026-09-28 has two faults (the agent 2b abort again, and the 1 MiB run doc); FRONTEND_BASE_URL was a configuration fix with no code change. Owner decisions: three postmortems (the two named plus FRONTEND_BASE_URL), the owner is interviewed for facts git cannot give, unknowns stay as `OWNER:` markers, no production code changes. Regression tests only where an existing seam allows it.

## Completed steps

(Each completed step is described here, written on the step's own branch before its PR is declared ready.)

### Step 0: Roadmap doc
This file, added via PR before any other work.

### Step 1: Cleanup and repo hygiene
Merged in PR #53 (branch `chore/repo-hygiene`). Removed the zero-byte tracked `[internal]` file and the stray `[internal]` line in `.dockerignore`; deleted the empty `tests/test_fetch.py` (covered by `tests/test_article_fetch.py`); pinned the unpinned tail of `requirements.txt` and `requirements-dev.txt`, resolved on `python:3.11-slim` to match Docker and CI; removed the redundant `pip install fastapi uvicorn` from the dockerfile; added a build-only `docker-build` job to `.github/workflows/tests.yml`. Verification and the repin procedure are recorded in the Step 1 plan above. Deviation from the roadmap: none beyond the extra findings listed in that plan. Caveat: the `docker-build` job only blocks merges if the branch ruleset requires it.

### Step 2: Eval foundations and labeling templates
Branch `test/eval-foundations`.

Built:
- `evals/stats.py`: `wilson_interval`, `rate_with_ci`, `cohens_kappa`, `paired_wins_losses` (scipy / scikit-learn wrappers).
- `evals/cost.py`: `estimate_cost` (price table keyed off `config.SCORING_MODEL`, extendable with `register_price`) and `CostGuard` (refuses estimates over 2 USD without explicit approval). The Haiku price in the table is list price from memory and should be checked against current pricing before a paid run relies on it.
- `evals/results.py`: results-file schema (`schema_version`, `name`, `created_at`, `git_sha`, `model`, `cost_usd`, `notes`, `metrics` where each metric must carry `value`, `n`, `ci_low`, `ci_high`) with writer, reader and validator. Results go in `evals/results/`.
- `evals/snapshot.py`: freezes the 2026-09-30 run's 500 articles joined with their audit rows into `evals/fixtures/articles_frozen.json` (ids, urls, titles, 300-character snippets, categories, confidence; no full text).
- `evals/make_label_templates.py`: seeded, deterministic generators for the two templates, which never fill the label columns.
- `evals/README.md`: conventions, layout and labeling instructions; `.gitignore` excludes `evals/fixtures/private/` (full text).
- Tests: `tests/test_evals_*.py` (27 new, stubbed, no network or keys). Total suite: 52 passing.
- `requirements-dev.txt`: `scipy`, `scikit-learn` and their dependencies, pinned from a `python:3.11-slim` resolve.

Templates (labels are written by hand by the repo owner):
- `evals/labels/agent1b_articles_template.csv`: 100 articles, 40 low-confidence (below 4) and 60 high-confidence, shuffled; fill `gold_category`.
- `evals/labels/summaries_template.csv`: 40 summaries (25 news written from full text, 5 news written from the description only, 10 papers); fill `supported`. Source text for each row is under the gitignored `evals/fixtures/private/summary_sources/` and is rebuilt by the script.

How to run (CMD): `venv\Scripts\python -m evals.snapshot`, then `venv\Scripts\python -m evals.make_label_templates articles`. For summaries, `set GCP_PROJECT_ID=<project>` after `gcloud auth application-default login`, then `venv\Scripts\python -m evals.make_label_templates summaries` (network fetches only, no LLM calls).

Deviations and caveats:
- The news summaries come from the June local file, so they reflect the prompt of that time and 5 candidates were skipped as unreachable (link rot); this set measures that prompt's faithfulness, not the current one's.
- The summaries file has no source text, so what the labeler sees was re-fetched now and may differ slightly from what agent2b saw.
- Many Hacker News articles have an empty snippet; label those from the title and url.
- The first-pass category is shown next to each article (per the roadmap), which can anchor the labeler.
- Selection recall cannot be evaluated, because the audit log only covers articles that survived selection.
- No eval results exist yet; nothing in this step produces a metric.

### Step 3: Review-step evaluation
Branch `feat/review-eval`. Built: `evals/review_eval.py` (scoring: accuracy per variant and stratum, calibration per confidence bucket, routing/cost, paired wins and losses), `evals/run_review_eval.py` (on-demand runner with `--dry-run`/`--approve`, `CostGuard`, gitignored fetch cache), `evals/make_readme_table.py` (regenerates the marked README block), with stub tests for each. No production code changed. The runner calls `filter_and_categorize` and `build_review_graph` directly on `evals/fixtures/articles_frozen.json`, with the production review cap lifted. Both arms categorize all 500 frozen articles; only the 100 gold-labelled ones are scored.

Run: `venv\Scripts\python -m evals.run_review_eval` (add `--dry-run` first), then `venv\Scripts\python -m evals.make_readme_table`. Results: `evals/results/review_eval_2026-10-05.json` (+ `_rows.json`), cost $0.32.

Results (n=90 scored by both arms; Wilson 95% intervals, see the README table): single-pass 62% (52-72%), graph first pass 59% (49-68%), graph after review 60% (50-70%). Graph final vs single-pass: 5 wins, 7 losses, 78 ties. Review vs graph first pass: 6 wins, 5 losses, 79 ties. Conclusion: the review loop does not measurably help on this sample; every difference is inside the noise. Verbalized confidence barely predicts errors (first-pass accuracy 17/32 below threshold vs 36/58 at or above, overlapping intervals; Spearman 0.12). 36% of labeled articles were routed to review at 1.0 tool calls each; 19% of reviews degraded to first pass (fetch failures).

Caveats: 10 of 100 gold articles were not re-selected by one of the arms and are excluded (coverage 90/100); n is small (34 low-confidence); frozen snippets are truncated to 300 chars, so both arms see less text than production; the labeler saw the first-pass category (possible anchoring); review fetches live pages. Two gold ids in the labels CSV were mangled by Excel and are re-matched by URL in memory (the file is unedited).

Deviation: the roadmap's alternative routing signal (evaluated when confidence is uninformative) was deliberately not done; the owner chose to report Step 3 as a null result. A candidate for later is single-pass vs graph first-pass disagreement, which `_rows.json` already supports without new API calls.

### Step 4: Prompt-injection tests
Branch `test/prompt-injection`.

Built:
- Deterministic tests (no network or keys, in CI): `tests/test_injection_render.py` (URL sanitizer, HTML escaping across all rendered fields and variants, attribute breakout, footer placeholders), `test_injection_prompts.py` (guard text on every Claude call in 1a, 1b x3, 2a, 2b, 3 x2; tag wrapping and breakout; output validation), `test_injection_fetch.py` (internal addresses blocked), `test_injection_sections.py` (refine guard, markup rejected on save, via FastAPI's `TestClient`), `test_evals_injection.py` (the harness itself, with a resistant and a compliant stub model). Suite: 161 passing, none xfail.
- Harness: `evals/injection_eval.py` (pure: case loading, judge, scoring), `evals/run_injection_eval.py` (on demand, `--dry-run`/`--approve`, `CostGuard`), `evals/fixtures/injection_cases.json` (23 hand-written cases over 1b categorize, 1b review loop, 2a, 2b, 3 selection, 3 intro and refine; attacks: forced category, tag breakout, instruction override, prompt leak, markup payload, SSRF steer). `evals/make_readme_table.py` also generates the README injection table.
- Fixes (one commit each): `agents/prompt_guard.py` (guard sentences centralised, wording unchanged, plus `neutralize_tags`); tag-like text neutralised before it enters 1b, 3 and review prompts; `_safe_url` and all rendered text pass through `_esc` (HTML escape plus `{{`/`}}` broken up); `filter_batch` validates category and index; `article_fetch.is_public_url` blocks loopback, private, link-local, metadata and `.internal`/`.local` hosts; refine endpoint gets a system prompt, `<topic>` tags and markup-stripped output; saving a section with `<` or `>` returns 422; `sections.html` renders names as text; one line added to `prompts/news_filter_prompt.txt` saying article text is data, not instructions.

How to run (CMD): `venv\Scripts\python -m pytest -q`; `venv\Scripts\python -m evals.run_injection_eval --dry-run`, then `venv\Scripts\python -m evals.run_injection_eval --name <name>` (needs `ANTHROPIC_1ST_API_KEY`), then `venv\Scripts\python -m evals.make_readme_table`.

Results (`evals/results/injection_eval_baseline.json` before fixes, `injection_eval_after.json` after; 115 attack and 115 control trials each; Wilson 95% intervals; see the README table): overall 10/115 attacks succeeded before (5-15%) and 0/115 after (0-3%); control 0/115 in both. Both baseline successes came from two cases at 5/5: a closing-tag breakout that forced "Canada & Montreal" in the categorize call, and a planted `localhost:8080` URL in an article description that the review loop fetched. Everything else (forced category without breakout, canary overrides, prompt-leak requests, markup payloads, refine) was not observed to succeed before or after. Cost: baseline $0.326, final after-run $0.321.

Honest reading of the two fixes: the tag neutralisation alone did not stop the categorize case (the injected sentence worked as plain text, so the tag was not the cause). It stopped only after the instruction-is-data line was added to the categorize prompt, and that case has n=5 per arm. The planted-URL result is partly a model-side non-fix: the model still asked for the planted URL in 5/10 trials after the fixes; what changed is that the fetch guard stops the request before the network (reached: 0/10).

Deviations from the plan and caveats:
- The harness was changed after the baseline: the first stub fetcher counted every URL the model asked for, and production had no fetch guard then, so that count equalled "reached". After the guard, the harness applies the production check and reports attempted and reached separately. The baseline file therefore has no `fetch_attempted` metric; the README table uses its `ssrf_steer` rate for that.
- Four paid runs were made in total (about $1.30): the baseline, an after-run with the old harness (identical to baseline, which exposed the stub issue), an after-run before the prompt line was committed (one judged success: the model refused and quoted the canary, so it was a judge false positive, since the judge flags any canary in the output), and the final committed run. Only the baseline and the final run are kept as results.
- The judge detects canary-style compliance only; it misses subtle steering and can false-positive on a refusal that quotes the canary. All controls were 0, so the control arm shows no base rate to read against. n per scope is 10-35, so intervals are wide and "0 observed" is not "safe".
- The SSRF check validates the URL as given: redirects and a DNS answer that changes between check and fetch are not covered. It fails open for hosts that do not resolve (the fetch fails anyway).
- The categorize prompt change affects production classification, so the Step 3 eval was re-run on it (`evals/results/prompt_recheck_review_eval.json` + `_rows.json`, cost $0.265, named so it does not overwrite the Step 3 results or change the README review table). Accuracy did not measurably change: single-pass 62% (52-72%, n=90) before vs 61% (50-70%, n=84) after; graph first pass 59% before vs 62% after; graph after review 60% before vs 61% after; every difference is inside the noise. Caveat: the scored set shrank from 90 to 84 gold articles, because fewer were re-selected by both arms (the selection step is sampled, and the new line may also make it slightly stricter; this run cannot tell which), so the two runs are not a clean paired comparison.
- Refine was exercised through FastAPI's `TestClient` with auth, tier and the Anthropic client stubbed, so no production refactor was needed. One refine control trial in the baseline run errored inside slowapi after the model had answered; its output was still judged.
- `sections.html` was checked on desktop Chromium with a stubbed `auth.js`: a hostile name rendered as text, no script ran, no horizontal overflow. The Android and iOS Playwright browsers (Chromium for Android, WebKit) are not installed here, so those were checked only as Pixel 7 and iPhone 15 viewport sizes in desktop Chromium, not in real mobile engines.
- Saved sections that already contain angle brackets would now fail validation when the config is next saved.
- Not done by design: CLAUDE.md and the remaining README prose (Step 9).

### Step 5: Drift monitoring
Branch `feat/drift-monitoring`.

Built:
- `agents/agent1b_fetch_news.py`: `agent1b_review_summary` (already merge-written to the run doc) now also carries `confidence_hist` (counts for 1-5 and `none`) and `category_counts` (by final category), computed by `drift.summarize_audit`. Additive; `news_filtered.json` and the other Firestore shapes are unchanged.
- `agents/drift.py` (pure, scipy imported lazily): KS on the 1-5 confidence distribution, chi-square on category mix (seeded permutation test when any expected count is under 5), Fisher exact on review rate, and `evaluate()`. A metric is flagged only if p < 0.01 **and** an effect floor is met (KS D >= 0.15, largest category share shift >= 10 points, review-rate change >= 10 points). Fewer than 3 usable prior runs gives `insufficient_history`. Thresholds are `DRIFT_*` constants in `config.py`.
- `agents/drift_history.py`: loads the latest runs (read-only); old graph-mode runs without the new fields fall back to their `agent1b_audits/{run_id}` doc; single_pass runs and runs without agent1b data are skipped.
- `agents/agent_healthcheck.py`: a drift section in the weekly email, built in its own `try/except`. It never changes the "all clear" / "problem detected" status and a failure inside it becomes a one-line note, so it cannot suppress the heartbeat. These are the healthcheck's first tests (`tests/test_healthcheck_drift.py`, with `tests/fakes_firestore.py`).
- `evals/backfill_drift_summary.py`: on-demand backfill of the two summary fields onto older runs from their audit docs (dry run unless `--apply`, no LLM calls).
- `evals/run_drift_null_sim.py` writes `evals/results/drift_null_simulation.json`.
- `requirements.txt` now pins `numpy` and `scipy` (moved from the dev file; same versions that resolved on `python:3.11-slim` in Step 2), so the runtime image grows.

Run (CMD): `venv\Scripts\python -m pytest -q`; `venv\Scripts\python -m evals.run_drift_null_sim`; backfill: `set GCP_PROJECT_ID=<project>`, `venv\Scripts\python -m evals.backfill_drift_summary` (dry run), then add `--apply`.

Results (simulation, 500 simulated weeks, each judged against 4 simulated prior weeks; Wilson 95% intervals): false alarms 0/500 overall (0-0.8%) and for each metric alone. Planted category shift detected: 10% of articles relabeled into one category 48% (44-53%), 20% relabeled 500/500 (99-100%). The 10% case sits right at the effect floor, so about even odds there is expected: the check is built to catch large shifts, not small ones.

Deviations and caveats:
- agent1a score drift was dropped by owner decision (only the top 3 papers are persisted), so there is no paper-score monitoring.
- The plan also mentioned `news_filtered.article_counts` as a fallback source for older runs; it was not used because it has no confidence data and would mix two definitions of "category mix". Backfill relies on `agent1b_audits` only, which exist for graph-mode runs only.
- The null simulation bootstraps one real week (the 2026-09-30 audit rows), so it has no real week-to-week variation. The 0 false alarms are therefore a lower bound on production false alarms, and with only about 4 baseline weeks the real rate is unknown until the check has run for a while.
- The confidence scale is discrete with heavy ties, so the KS p-value is approximate; the effect floor does the real work there.
- The check reports and never alerts: a drift flag does not change the email subject.
- **Verified:** the healthcheck image builds on `python:3.11-slim` and `scipy`, `numpy`, `drift`, `drift_history` and `agent_healthcheck` import inside it (Python 3.11.16, scipy 1.17.1, numpy 2.4.6). A read-only backfill dry run against the real Firestore examined 38 runs: only 2 have graph-mode data (`2026-10-05T100004Z`, `2026-09-30T201701Z`); the other 36 predate the LangGraph change or never reached agent1b, and agent3 had pruned their per-article data. With 1 usable prior run, the drift section will report "not enough history" until about 3 more weekly runs carry the fields (roughly mid-November). The backfill cannot shorten that, because the older data no longer exists.
- **Not done, by decision:** the backfill `--apply` was not run (it would add the fields to just those 2 production run docs; the owner chose to skip it), and the drift section has not been rendered from real run history, only from test fakes. **Deployed 2026-10-05:** the Step 5 images were built with Cloud Build from the merged `main` and all 10 Cloud Run services (agent1a, agent1b, agent2a, agent2b, agent3, agent4, agent4-test, orchestrator, agent-subscriptions, healthcheck) were updated with `gcloud run services update --image`, which created a new revision for each. The drift section reports "not enough history" until about 3 more weekly runs carry the fields.

### Step 5b: Token and cost monitoring
Branch `feat/cost-monitoring`.

Built:
- `agents/tracing.py`: `make_client(agent, run_id, **client_kwargs)` tags every traced Claude call with `agent:<name>` and `run:<run_id>` (plus metadata) via `wrap_anthropic(tracing_extra=...)`. Agents 1a, 1b (graph), 2a, 2b and 3 now build their client through it and call `tracing.flush()` in a `finally`. Still opt-in and a no-op without the key and flag.
- `agents/usage_archive.py`: finds a run's LLM calls in LangSmith by the `run:<run_id>` tag, sums tokens and LangSmith's `total_cost` per agent, and writes `llm_usage` onto the run doc (idempotent, writes nothing if LangSmith has no calls so a later run can retry inside the 14-day window, one failed run does not stop the others).
- `agents/drift.py`: `usage_totals` (tokens, LangSmith cost, list-price estimate) and `usage_ratio_test` / `evaluate_usage`: total tokens, total cost and per-agent tokens are flagged when they sit at least 50% from the median of the prior runs and the absolute change clears a floor (100k tokens, $0.10). No p-values: four baselines are too few. Needs 3 archived prior runs, otherwise "not enough history".
- `agents/agent_healthcheck.py`: `_usage_section` archives this run, then shows tokens, LangSmith cost and the list-price estimate side by side, per agent, plus the drift verdict. It never raises, never changes the all-clear or problem status, and cannot suppress the heartbeat. Without `LANGSMITH_API_KEY` on the healthcheck service it reports "skipped".
- `agents/pricing.py`: the price table moved out of `evals/cost.py` (which now imports it) and gained cache pricing. Haiku 4.5 verified against Anthropic's pricing page on 2026-10-05: $1 input, $5 output per million tokens. The email labels the estimate with that date.
- `tests/conftest.py` now clears LangSmith env vars for every test. This was needed: the developer shell had `LANGSMITH_API_KEY` set and the new healthcheck tests would otherwise have reached the real LangSmith API (they hung instead of running). Suite: 204 passing (182 before this step).
- `evals/run_usage_drift_sim.py` writes `evals/results/usage_drift_simulation.json`.

Run (CMD): `venv\Scripts\python -m pytest -q`; `venv\Scripts\python -m evals.run_usage_drift_sim`.

Results (simulation, 2000 simulated weeks per setting, Wilson 95% intervals): there is no history of real week-to-week variation yet, so noise is assumed (normal, centred on the token count of the one real agent1b run). False alarms: 0/2000 at 5% and at 10% weekly noise (0-0.2%), 3.7% at 20% noise (3.0-4.6%), 15.7% at 30% noise (14.2-17.4%). Detection at 10% noise: a +30% jump 10% of the time (9-12%), +60% 73% (71-75%), +100% 99% (99-99%). The rule is built to catch large jumps; if real weeks vary by 30% it will cry wolf about one week in six, and the thresholds should then be loosened. This is a property of assumed noise, not a measured production rate.

Deviations from the plan and caveats:
- Usage is aggregated over LLM-type runs found by the tag filter (`has(tags, "run:<id>")`, the filter LangSmith's docs show), not over root traces or metadata, because tag propagation to root runs and the metadata filter grammar were not verifiable offline. It uses `Client.list_runs`, which the pinned `langsmith==0.14.2` documents as deprecated (removal after 2027-01-31); it sits behind one wrapper (`_list_llm_runs`) so it can be swapped for `Client.runs.query`, which needs a newer LangSmith backend.
- The plan expected about 6 traces per weekly run (one root per agent). As built, agents 1a, 2a, 2b and 3 do not wrap their run in a root trace, so each Claude call is its own root trace: by my count of call sites, on the order of 100 per weekly run (a rough estimate, not measured), plus agent1b's graph run. That is still well under the 5k a month free allowance, but the allowance is shared with any other project on the same LangSmith account.
- The LangSmith API was inspected offline and exercised only through fakes. Whether LangSmith fills `total_cost` and the token fields for the wrapped Anthropic calls, and whether its price list includes `claude-haiku-4-5-20251001`, is not confirmed until real traced runs exist. The email shows "n/a (not every call priced)" for LangSmith's cost if any call lacks a price, and the drift test then falls back to the list-price estimate.
- Free-tier facts come from langchain.com/pricing and third-party summaries that disagree on whether usage is hard-capped until a card is added and on extended retention; confirm in LangSmith Settings > Billing.
- agent1b `single_pass` mode (the rollback path) and the subscriptions refine endpoint are not traced.
- History starts at the first run after deploy and secret mounting, so expect "not enough history" for about three weekly runs. Only agent1b's own `token_usage` on older run docs exists from before; it is not backfilled (LangSmith only holds 14 days and the other agents were never traced).
- **Deployed 2026-10-05:** PR #60 merged (`78c9d88`), images built from `main` (Cloud Build `2fd8e954`), and agents 1a, 1b, 2a, 2b, 3 and the healthcheck updated. Agents 1a, 2a, 2b and 3 now get `LANGSMITH_API_KEY` (from the existing `langsmith-api-key` secret), `LANGSMITH_TRACING=true` and `LANGSMITH_PROJECT=latent-spacemail-prod`, the same values agent1b already had; the healthcheck gets the key and project only (it reads from LangSmith and does not send traces). Updates used `--update-secrets` and `--update-env-vars`, never `--set-*`. The owner confirmed the `latent-spacemail-prod` project already had traces in it, which shows the existing key works.
- **Still unverified until the first traced weekly run (2026-10-12):** that agents 1a, 2a, 2b and 3 actually send traces carrying the `agent:` and `run:` tags, that LangSmith fills in token counts and `total_cost` for the wrapped Anthropic calls and prices `claude-haiku-4-5-20251001`, and the real number of traces per run against the free plan's 5k a month (shared across the LangSmith account). Until then the healthcheck's usage section will report "no traced Claude calls found" if tracing is not producing data. No traced run has been inspected in the LangSmith UI, and the first healthcheck after that run is where the archive and usage section run for real.

### Step 6: Online judge
Branch `feat/online-judge`. Status: code written and stub-tested (240 tests passing); calibrated on 2026-10-06; nothing is deployed.

Built:
- `agents/judge.py` + `prompts/judge_prompt.txt`: binary faithfulness verdict (tool use, guard text, tag neutralising). Model `JUDGE_MODEL = claude-sonnet-5-5` in `config.py`; list price $2 / $10 per MTok registered in `agents/pricing.py`, checked against the pricing page on 2026-10-06.
- `agents/summary_sources.py`: agents 2a and 2b persist the exact source text each summary was written from to Firestore `summary_sources` (one doc per item, 21-day expiry, a failed write never fails the agent). Additive; no orchestration change.
- `agents/online_judge.py`: weekly sample of at most `JUDGE_MAX_ITEMS`=12 shipped summaries (papers first, then a seeded news sample stratified by `used_fallback`), cost estimate checked against `JUDGE_MAX_USD`=0.25 before any call, `judge_results` (counts and verdicts only, no text) written to the run doc, idempotent.
- `agents/drift.py`: `judge_unsupported_test` / `evaluate_judge` (Fisher exact, p < 0.01 and a 15-point rise, 3 prior judged runs needed).
- `agents/agent_healthcheck.py`: a summary-faithfulness section in the weekly email, in its own `try/except`. Report-only while `JUDGE_ALERTING_ENABLED` is False; it cannot suppress the heartbeat.
- `evals/judge_eval.py`, `evals/run_judge_calibration.py`: kappa (bootstrap interval), agreement, recall and precision for "unsupported", per-source agreement; `--dry-run` estimates about $0.38 for the 40 rows. `evals/README.md` explains how to label `supported`.
- `evals/run_judge_drift_sim.py` -> `evals/results/judge_drift_simulation.json` (assumed true unsupported rates, 12 items a week, perfect judge assumed): false alarms 0.3% to 0.4% at 5%, 15% and 30% assumed rates (about 0.1% to 0.9%); a rise from 10% to 30% unsupported is caught in 16% of weeks (14-17%), to 50% in 61% (58-63%), to 70% in 94% (93-95%). It catches only large jumps at this sample size.

Run (CMD): `venv\Scripts\python -m pytest -q`; `venv\Scripts\python -m evals.run_judge_drift_sim`; after labeling, `venv\Scripts\python -m evals.run_judge_calibration --dry-run`, then without `--dry-run`.

Calibration result (`evals/results/judge_calibration.json` + `_rows.json`, run 2026-10-06 on the final prompt, 40 labeled rows, cost $0.56; Wilson 95% intervals, kappa bootstrap interval):
- Agreement with the owner's labels 45% (31-60%), Cohen's kappa 0.04 (-0.15 to 0.23): no agreement beyond chance, below the 0.6 working bar.
- The judge flagged 24 of 40 summaries (60%) as unsupported; the owner flagged 6 of 40 (15%). Confusion: both unsupported 4, owner unsupported and judge supported 2, owner supported and judge unsupported 20, both supported 14. Recall on the owner's unsupported rows 4/6 = 67% (30-90%); precision 4/24 = 17% (7-36%); false-alarm rate on the owner's supported rows 20/34 = 59% (42-74%).
- By source: description-only 40% (n=5), full text 44% (n=25), PDF text 50% (n=10). All intervals are wide.
- Conclusion: the judge as written does not reproduce the owner's labels, and it must not alert. `JUDGE_ALERTING_ENABLED` stays False; the weekly section is informational.
- Reading the disagreement honestly: I read the claims the judge gave on 8 of the 20 rows the owner marked supported. Many point to specific, checkable gaps (a company described as "is raising" funds where the source says it is in early talks; "launched" where the source says pre-order; "first error rates" where the source says "as far as we are aware"; a stated result described as the opposite of what the paper reports). So part of the gap looks like a stricter reader, not only judge error. On one row the owner confirmed they marked a "Chinese-made" chip claim supported from outside knowledge although the source never says it, which the labeling rubric says not to do; that disagreement is a labeling slip, not a judge error, and other rows may have the same issue. The labels were left as submitted. Which side is right on the remaining disagreements was not adjudicated, so this result cannot say whether the judge or the labels are closer to the truth, and the owner's 6 of 40 should not be quoted as a pipeline rate. If the owner revises labels, the result can be re-scored offline from `judge_calibration_rows.json` at no API cost.
- Not done by design: the prompt was not tuned against these 40 labels (that would make any later agreement on them meaningless). A prompt change would need a fresh labeled set, or an adjudicated second pass, to be evaluated.

Problems found while running it, fixed before the result above: (1) `claude-sonnet-5-5` rejects a forced `tool_choice` (400, types "tool" and "any" not allowed), so the first version of the judge would have errored on every weekly item; the stub tests could not catch it, and `tool_choice` is now `auto`. (2) The model emits a thinking block that counts against `max_tokens`; at 600, 5 of 40 replies were cut off before the tool call. `JUDGE_MAX_TOKENS` is now 2000, a reply that stops on `max_tokens` is treated as an error rather than trusting a truncated verdict, the cost estimate allows 700 output tokens per item, and `JUDGE_MAX_USD` rose from 0.25 to 0.40 (worst case: 12 items of 5000 words is about $0.30). (3) A unit test called the real calibration runner once the label file was fully filled, so a plain `pytest` run made real API calls (about $0.44) and wrote a results file; that run, with its 5 errors, was discarded, the test now stubs the label loader, and the real run above was made deliberately. Total API spend for the step is about $1.0.

Still open: (a) deployed 2026-10-06: all 10 services were rebuilt from `main` (Cloud Build, then `gcloud run services update --image` on each), so agents 2a and 2b now save sources; (b) NOT done: the Firestore TTL rule `gcloud firestore fields ttls update expires_at --collection-group=summary_sources --enable-ttl`; (c) by decision the healthcheck has no Anthropic key mounted, so the judge section reports "skipped" until the owner chooses otherwise (the judge did not validate, and at worst costs about $0.30 to $0.40 of Sonnet a week). Not verified: the weekly path against real persisted sources; the first real run after the deploy is 2026-10-12, and this entry is updated and the box ticked after it.

Rubric change (2026-10-06, before any labels were scored): the first judge prompt required every claim, including the "why it matters" sentence, to be in the source. The summarizer prompts explicitly ask for that significance sentence (`prompts/paper_summary_prompt.txt` paragraph 2, `prompts/news_summary_prompt.txt` "most important implication"), so the strict rule would have flagged most summaries. The judge prompt and the labeling instructions now treat factual claims strictly and significance sentences leniently (no new specific fact, no contradiction, no overstatement). The owner and the judge apply the same rule; borderline ids are kept on a side list.

Caveats: n=40 labels, so kappa will have a wide interval; the judge is the same model family as the summarizer; the labeler saw the same source and summary; the template's sources were re-fetched when it was built, so they may differ slightly from what the summarizer saw; unsupported summaries are probably rare, so recall is very uncertain. The weekly sample is about a dozen items, so the drift check is a tripwire, not a guarantee. Sonnet 5.5 uses a newer tokenizer (about 30% more tokens per text than Haiku 4.5), so the cost estimate uses a rough tokens-per-word figure and the real cost is taken from reported usage.

### Step 7: Click-through signal
Branch `feat/click-signal`. Status: code written and tested (313 tests passing, all stubbed, no network); the image builds on `python:3.11-slim` and the new modules import in it; nothing is deployed and no real SendGrid event has been received. The checklist box is `[~]` until the deploy and an end-to-end test send are done.

Built:
- `agents/sendgrid_webhook.py`: `verify_signature` (ECDSA P-256 over timestamp + raw body, 10 minute window, runs before any parsing) and `reduce_events` (click events carrying our `run_id` only, reduced to `run_id`, `url`, `timestamp`, `bot`; email, IP, user agent, message and event ids are read only to flag bots and are never copied out). Uses `cryptography`, already pinned.
- `agents/click_links.py`: URL normalisation and the link map built from `news_summaries` and `paper_summaries`; clicks on anything not shipped (footer, unsubscribe, preferences) are ignored.
- `agents/click_counts.py`: `click_links/{run_id}` and `click_counts/{run_id}` storage, bucketing and atomic increments. Buckets: `clicks` (headline), `early` (within 5 minutes of the send start, when mail scanners prefetch links) and `bots` (automated user agents).
- `agents/agent_subscriptions.py`: `POST /sendgrid/events`, rate limited, 403 on a bad or stale signature, 503 if the key is not configured, 400 on a body that is not a JSON array, and always 200 once verified (a counting error is logged by class name only) so SendGrid does not retry.
- `agents/agent4_send.py`: `CLICK_TRACKING=true` stores the link map and sends each email with `tracking_settings.click_tracking` on and `custom_args {run_id}`; the tag is the run, never a subscriber. Flag unset gives the exact previous payload (the rollback). A failure while preparing tracking sends everything untracked. This was the first test coverage of agent4's send loop.
- `agents/click_report.py` and the healthcheck: a "Reader clicks" section with this week so far, the last tracked weeks' totals, clicks per delivered email, the early and bot buckets, per-category split and the top 3 articles for the last tracked week. Built in its own `try/except`; informational, it never changes the all-clear status and cannot suppress the heartbeat.

Run (CMD): `venv\Scripts\python -m pytest -q`. There is no paid or on-demand eval in this step.

Deviations from the plan: bot clicks are counted in their own `bots` bucket instead of being dropped, so the email can show how much noise there is; the run id is the `pipeline_runs` document id (what the healthcheck keys on); `_load_latest_newsletter` now returns a `LoadedNewsletter` tuple (variants, subject, run id, doc) with one caller; agent4 had no tests, so the send loop got stubbed ones.

Deployed 2026-10-06 (the owner did the SendGrid and secret steps; the services were rebuilt and updated): the Event Webhook exists (Clicked only, signature verification on), its verification key is stored and available to agent-subscriptions, all services run the merged code, `CLICK_TRACKING=true` is set on agent4 and agent4-test, and an unsigned request to `/sendgrid/events` returns 403 while SendGrid's "Test Your Integration" call returned 200 (checked in the Cloud Run request log), so the signature check works against SendGrid's real signing. The owner confirmed account-level click tracking was already on in SendGrid, so readers already received SendGrid-wrapped links before this step.

Still open: no real tracked email has been sent and no real click counted. agent4 refuses a newsletter older than 24 hours, and the last composed one was already stale when this was deployed, so the first tracked send is the real one on 2026-10-12 at 7 AM; the owner chose to save the cost of a manual pipeline run. After that send: confirm the agent4 log line "click tracking on", the `click_links` and `click_counts` documents, the real shape of SendGrid's events (for example whether `url` arrives HTML-decoded, which `normalize_url` tolerates either way), and the healthcheck's clicks section. Then record the result here and tick the box.

Caveats: under 50 readers makes this a rough signal; counts are not unique per reader (that would need per-subscriber state), and SendGrid can deliver an event more than once, so a click can occasionally be counted twice; scanner and bot clicks inflate counts and are only partly filtered; position and layout bias are not corrected; the webhook handler sees subscriber emails and IPs in memory by design, and the guarantee that none are stored or logged is enforced by tests (`tests/test_sendgrid_webhook.py`, `tests/test_click_endpoint.py`), not by SendGrid; link branding is not set up, so clicks go through SendGrid's default tracking domain, which can affect deliverability and how links look; Cloud Run's request logs still record caller IPs for the webhook route as they do for every route.

### Step 8: Postmortems and runbook
Branch `docs/postmortems-runbook`. Status: written and tested; waiting on owner review.

Built:
- `docs/postmortems/`: `TEMPLATE.md`, an index `README.md`, and three postmortems: `2026-09-07-stale-newsletter.md`, `2026-09-28-firestore-doc-size.md`, `2026-09-11-frontend-base-url.md`. Facts come from CLAUDE.md, commit messages and the code, plus the owner's answers (the owner noticed the duplicate issue; all active subscribers received it; no complaints; no agent 2b abort seen since 2026-09-28; start date of the wrong `FRONTEND_BASE_URL` unknown). The native abort root cause is stated as unconfirmed.
- `docs/runbook.md`: health check messages mapped to causes and first checks, finding the stalled stage, re-running a stage with a Pub/Sub-style envelope (so the run id is kept), rollback switches, key rotation without naming secrets, known gaps. Verified against `main.py`, `agent_healthcheck.py` and both `increment_and_check` functions.
- `tests/test_incident_regressions.py` (16 stubbed tests): `parse_started_at`, the healthcheck with a naive `started_at`, agent 4's freshness guard, footer links built from `FRONTEND_BASE_URL`, the watchdog deadline (before, at and after 07:30), `_run_isolated` (recovery clears the error; all attempts dying by signal records a crash), and agent 2b's idempotent counter. `tests/fakes_firestore.py` gained an equality `where`. Suite: 329 passing.

Findings while writing the runbook (not fixed, no production code changed): re-running agent 2a or 2b on a run whose counter has already reached 2 publishes `content-summarized` again and so starts agent 3 a second time; agent 2a's increment is not idempotent, and agent 2b's returns the existing count (still 2 or more).

Open: (1) done after owner approval: the pruning was extracted unchanged from `run()` into the pure `build_run_doc_update()` in `agents/agent3_compose.py`, and a test checks that ~500 articles plus four variants stay under 1 MiB (measured as JSON size, a proxy for Firestore's own size accounting) while the unpruned shape exceeds it (suite: 329 passing); (2) `OWNER:` markers in the postmortems (send time and subscriber count of the 09-07 send, whether a newsletter went out the week of 09-28, how the 09-28 failure and the dead link were detected) for the owner to fill in; (3) the file date of the FRONTEND_BASE_URL postmortem is the fix date, as the start is unknown.
