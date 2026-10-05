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
- [ ] Step 3: Review-step evaluation (single_pass vs graph)
- [ ] Step 4: Prompt-injection tests
- [ ] Step 5: Drift monitoring
- [ ] Step 6: Online judge (calibrated weekly scoring and alerting)
- [ ] Step 7: Click-through signal (SendGrid)
- [ ] Step 8: Postmortems and runbook
- [ ] Step 9: Results, README, CLAUDE.md, retire this doc
- [ ] Optional A: Model card and privacy review (Law 25 / GDPR)
- [ ] Optional B: Agent 2b verify loop (generate, verify, retry or fall back)

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
