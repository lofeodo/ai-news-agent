# Cost reduction: cheaper generation, then re-benchmark

A living plan. **One step = one PR, one step at a time.** When a step starts it gets a detail section; when it merges, the box is ticked and the detail is replaced by a short write-up with the measured result. Cost and quality numbers come from `evals/`, never typed by hand.

## Problem

A weekly run costs about **$2.08** in Claude calls (LangSmith estimate, mean of 2 archived runs, `evals/results/pipeline_cost.json`, all on Haiku 4.5 at $1 / $5 per M tokens):

| Agent | $ / run | Calls | Tokens | Share |
|---|---|---|---|---|
| agent2b (summarize news) | 0.84 | ~490 | 669k | 40% |
| agent1b (fetch, filter, categorize, review) | 0.65 | ~130 | 479k | 31% |
| agent1a (score papers) | 0.47 | 35 | 423k | 23% |
| agent3 (select, intro, dedup) | 0.12 | ~15 | 115k | 6% |
| agent2a (summarize paper) | 0.02 | ~2 | 15k | 1% |

Most of the money is **volume**, not model quality: ~490 one-article calls in agent2b, and 35 full-PDF scoring calls in agent1a. That shapes the order below: cut waste first (free, no quality risk to measure), then change models.

## Principles

- Baseline first, and freeze it. Every later number is compared to Step 1's.
- Quality gate per step: no step ships if its eval arm is worse than baseline beyond the noise the existing evals show (they use small label sets, so say "not distinguishable" when it is).
- Each step has its own rollback switch (env var or a revert) so no step needs a code redeploy to undo, where possible.
- Public article text is the only thing ever sent to a third-party model provider. Subscriber data never leaves the existing services.

## Step sequence

- [x] Step 1: Freeze the baseline (cost + quality), no behavior change
- [x] Step 2: Turn off the agent1b review loop
- [ ] Step 3: Cut call volume (agent2b shortlist and agent1a abstract-first are live; still open: agent1b language filter, prompt caching, batch API)
- [ ] Step 4: Verify model prices and pick candidates (research, no code)
- [ ] Step 5: Provider abstraction (one `llm` client seam, Claude stays the default)
- [ ] Step 6: Per-stage model benchmark (cheap models vs Haiku baseline)
- [ ] Step 7: Adopt the winners per stage behind per-stage env switches
- [ ] Step 8: Final re-benchmark against the Step 1 baseline

**Status (2026-10-16):** weekly cost $2.06 -> $1.05 (-49%, one production debug run); the 2026-10-18 release run is the confirmation. Next: the agent1b language filter, then decide whether the model steps (4-8) are worth it for what is left.

Call-volume cuts (Step 3) go before the model work, per the owner (2026-10-15): they are risk-free and shrink what the model benchmark has to cover. The model steps (4-6) then run against the already-reduced volume.

## Step detail

### Step 1: Freeze the baseline (done 2026-10-15)
**Cost (frozen):** `evals/results/baseline_cost_2026-10-15.json`, from `python -m evals.fetch_pipeline_cost` (git `8a139f4`): mean of **4** archived production runs, LangSmith estimate, Claude calls only, min/max in place of an interval.

| Agent | $ / run (min-max) |
|---|---|
| agent2b | 0.805 (0.774-0.856) |
| agent1b | 0.637 (0.624-0.661) |
| agent1a | 0.467 (0.464-0.471) |
| agent3 | 0.132 (0.114-0.149) |
| agent2a | 0.014 (0.010-0.021) |
| **Total** | **2.055 (2.022-2.114)**, ~656 calls |

**Quality (frozen, already in `evals/results/`, no new API spend):**

| Stage | Baseline file |
|---|---|
| agent1b categorize / review loop | `review_eval_2026-10-05.json`, `prompt_recheck_review_eval.json` |
| agent3 dedup | `dedup_eval_2026-10-08.json`, `dedup_eval_2026-10-09.json` |
| Prompt injection | `injection_eval_after.json` |
| Summary judge (report-only, kappa 0.04) | `judge_calibration.json` |

**Gap found:** there are no frozen fixtures or labels for **agent1a paper scoring** or **agent2b/2a summaries**, and those are exactly the stages Step 3 changes. Building them (a frozen article set and a frozen paper set with Haiku's current outputs as the reference, plus rank agreement and the report-only judge as metrics) is the first task of Step 3, before any change to those agents, so the "before" is captured on the unmodified code. (Done: Step 3 sub-step 1.)

### Step 2: Turn off the review loop (done 2026-10-15)
The review eval says it plainly: confidence does not predict errors (Spearman 0.117, intervals overlap), and the loop's final accuracy (0.600) is not distinguishable from the single pass (0.622) or from its own first pass (0.589).
- **Shipped:** `ReviewConfig.from_env()` now defaults `REVIEW_MAX_ARTICLES` to 0 (`DEFAULT_REVIEW_MAX_ARTICLES` in `agents/agent1b_graph.py`), so the route node sends nothing to `review`. The graph nodes, the audit and the eval are untouched and reproducible. Rollback with no redeploy: `gcloud run services update agent1b --update-env-vars REVIEW_MAX_ARTICLES=30`. Deployed 2026-10-15 as Cloud Run revision `agent1b-00044-9z8` (image built from `c2d78d8`).
- **Expected saving:** ~$0.03-0.05 a run (0-30 extra calls and fetches), to be confirmed from the first run's LangSmith cost, not measured yet.
- **Confidence field dropped with it (owner, 2026-10-15):** when review is off (`max_articles == 0`), `categorize` calls `filter_and_categorize(with_confidence=False)`, so the tool schema and prompt carry no confidence and nothing is routed or "capped". Turning review back on (`REVIEW_MAX_ARTICLES>0`) restores the confidence tool and addendum. The earlier plan to gate this on a categorize accuracy check was waived: nothing reads the field. No accuracy re-check was run, so the first run's category counts are the thing to eyeball.

### Step 3: Cut call volume
**Sub-step 1 done 2026-10-15: frozen "before" (no behavior change).** `python -m evals.freeze_baseline summaries|papers` runs the unmodified `agent2b.summarize_article` and `agent1a.score_paper` on fixed inputs and records what Haiku produces. Tracked: `evals/fixtures/summaries_frozen.json`, `evals/fixtures/papers_frozen.json`. Source text and PDFs are third-party and stay in the gitignored `evals/fixtures/private/`.
- **Articles:** 60 from the 2026-10-11 release run's persisted `summary_sources` (all 30 shipped, each with its production summary, plus 30 seeded-random others). 60/60 summarized, 11 on the description fallback, mean 56.4 words, $0.099 for the set (1,246 input / 79 output tokens per article). Language is inferred for the 30 non-shipped ones (it was not stored); 2 of 60 are French.
- **Papers:** the 35 most-upvoted unspotlighted HF Daily Papers on 2026-10-15, all scored from the full PDF (median 92k characters, none fell back to the abstract), scored **twice**. The two passes of the same code agree at Spearman **0.77** and share **5 of the top 8** (0.625); the mean total differs by 0.9 points (max 4). Haiku's scoring is noisy, so any agent1a change is judged against that floor, not against a perfect match. $0.96 for both passes (~12.0k input tokens per call, close to the plan's ~12k).
- **Metric helpers** in `evals/freeze_baseline.py`: `spearman`, `top_k_overlap`, `summarize_outputs` (success, fallback, words, tokens). The report-only judge applies to summaries as before (kappa 0.04, so it is a smoke test, not a gate).
- **Consequence for the plan:** the abstract-first design for agent1a must be judged on whether the pick the owner would make stays in the top 8, not on exact rank, because the baseline itself only reproduces 5 of 8.

**Sub-step 2: agent2b shortlist mode (built 2026-10-15; live since 2026-10-16).** `AGENT2B_MODE=shortlist` (default `all`, today's behavior untouched; switch with no redeploy). Stage 1 reuses agent3's own selector on title + HN points + the first ~300 characters of the page (HN stories have no description in any of the 264 HN articles of the frozen pool, so the fetched text is the only content) and keeps picks + runners-up for both language passes; only those are summarized; agent3 then chooses from real summaries. A failed category is kept whole.
- **Eval** (`python -m evals.run_shortlist_eval`, frozen 500-article pool, unmodified selector, both passes; $1.13 to run): 82 of 500 articles shortlisted. Modelled agent2b cost per run **$0.88 -> $0.30** (about $0.59, 67% less).
- **Quality:** of the baseline's picks, **74%** [60-84%] also appear when selecting from the shortlist, against **89%** [77-95%] when Haiku simply repeats the baseline selection on the same input (n=46 picks). 93% [83-98%] of baseline picks survive stage 1. The intervals overlap, so the loss is not statistically distinguishable at this size, but the point estimate is about 15 points lower, which is roughly 1 more swapped pick per section. In the "all" pass five categories each swapped exactly one pick; the repeat arm swapped one in two. Swaps looked like near substitutes, with one concern: a high-HN model release (586 points) lost its slot.
- **Real side-by-side (2026-10-16, local run, one shared pool of 547 articles, nothing deployed):** agent1b once, then agent2b + agent3 in `all` mode twice (agent3 re-run on the same summaries) and in `shortlist` mode. The shortlist run summarized **81 of 547** articles (agent2b 87 s against 248 s; agent3 35 s against about 65 s). Of the 23 articles in the baseline's full newsletter, the shortlist run kept **17 (74%)**, and 13 of 20 in the plain variant; the second baseline kept 22 of 23 (17 of 20). Five of seven sections each swapped one pick, a few more in Industry & Business.
- **Why the picks move, tested:** 5 of the 6 lost articles were *in* the shortlist (recall is not the problem; the final editor chose differently). Carrying each article's HN rank from the whole section into the smaller pool (a plausible cause, since agent3 labels "#n of N HN stories in this section") changed nothing (16 of 23). It was kept anyway at the owner's request (2026-10-16), for consistency with the full run: `news_shortlist.hn_context` plus a branch in agent3's `_hn_label` that only applies when the fields are present. Giving agent3 the baseline's own summaries for the same 81 articles also gave 17 of 23, so summary re-generation is not the cause either. What is left is the pool itself: choosing from about 12 articles per section instead of about 100, in a different order, moves the near-tied picks. The "second baseline" figure above is therefore not a fair noise floor (same pool, same order); a fair one needs a run that also changes the pool.
- **Reading the swaps (variant 1_1):** mostly comparable stories (Krea 3 vs Gemini 4 Argon for Robotics, FTC subpoenas vs eight states suing OpenAI, two funding stories vs a $445M Series D and Anthropic's revenue disclosure). One moves against the editor's own HN criterion (a 741-point post replaced by a 48-point one). Whether that is worse is for the owner to judge from `data_runs/` (all_a, all_b, shortlist: the four HTML variants of each; local and untracked).
- **Decision (owner, 2026-10-16): enable it.** About a quarter of the picks differ on a real week, in a way the eval could only hint at; the owner accepted that for about $0.59 a week. Rollback is `AGENT2B_MODE=all`. Switching it on needs agent2b deployed with `AGENT2B_MODE=shortlist`; agents do not deploy, so that is a deploy request to the owner. First real run is the Sunday release run, so check the Monday health check and the issue, and revert if the picks look worse.
- **Also confirmed locally:** `AGENT1A_MODE=abstract_first` ran end to end (35 of 35 scored in 82 s, one PDF-scored second pass), and agent1b with review off routed nothing to review and asked for no confidence. agent1b's language filter is the next large line (62 calls, about 220k input tokens in that run).
- **Not covered:** duplicate removal (follows selection, evaluated separately) and page text for the frozen pool was fetched live a week or more after the originals.

**Sub-step 3: agent1a abstract-first scoring (built 2026-10-15; live since 2026-10-16).** `AGENT1A_MODE=abstract_first` (default `full`; `AGENT1A_FULLTEXT_TOP_K`, default 8; no-redeploy switch). Stage 1 scores all 35 papers from the abstract (the existing PDF-failure fallback path, so no new prompt); stage 2 downloads and scores the PDFs of the top K. Full-text-scored papers always rank above abstract-only ones.
- **Eval** (`python -m evals.run_abstract_first_eval`; the 35 frozen papers with two full-text passes as reference, two new abstract-only passes; $0.26): the paper full-text scoring ranks first sits at **rank 5** in all four abstract rankings, and the full-text top 3 (identical in both full passes) are all inside the abstract top 5 in every comparison. Abstract-vs-full Spearman 0.69, against 0.77 between the two full-text passes. Two abstract passes share 7 of their top 8.
- **Cost per run** (modelled from measured tokens): $0.48 all full text -> **$0.24 at K=8** ($0.20 at K=5), about half. It also skips 27 PDF downloads through the Squid proxy.
- **Caveat:** one week of papers and a reference that is one noisy model's opinion. The result says the abstract carries the signal Haiku uses, not that the paper is the one the owner would pick.
- **Decision (owner, 2026-10-16): enable it**, K=8 (rank 5 leaves little margin at K=5). Needs agent1a deployed with `AGENT1A_MODE=abstract_first`; a deploy request to the owner. Rollback is `AGENT1A_MODE=full`.

**Deployed and switched on 2026-10-16** (owner approved): agent1a `abstract_first`, agent2b `shortlist`, agent3 with the carried HN rank, all from `main` at `f596a11`; existing env vars untouched. First release run on them is 2026-10-18.

**Measured on a production debug run (2026-10-16, run `2026-10-16T135631Z`, deployed code with both modes on, review off; LangSmith's estimate for this run's calls only):**

| Agent | Baseline $ (mean of 4) | This run $ | Calls (base -> now) |
|---|---|---|---|
| agent1a | 0.467 | 0.238 | 35 -> 43 |
| agent1b | 0.637 | 0.498 | 128 -> 77 |
| agent2a | 0.014 | 0.011 | 1 -> 1 |
| agent2b | 0.805 | 0.263 | 470 -> 93 |
| agent3 | 0.132 | 0.041 | 21 -> 17 |
| **Total** | **2.055** | **1.051** | **656 -> 231** |

That is **49% less** (about $1.00 a week, roughly $52 a year). The run composed with no `*_error` field; agent1a ran 8 second-pass papers, agent2b shortlisted 80 of 547 articles. agent3 got cheaper on its own (123k -> 31k tokens) because it now reads a pool of ~80 summarized articles instead of ~470. One run, so the release run on 2026-10-18 is the confirmation. agent1b is now the largest line: its language filter is about $0.34 of the $0.50.

**Sub-step 4: agent1b language filter (added 2026-10-16, owner).** The filter is necessary: it keeps articles in languages other than English and French out of the newsletter. The owner's rule is that it must keep doing that, and that its cost should still come down if it can without letting foreign-language articles through.
- **What it costs today** (local run 2026-10-16, 547 articles, review off): the language filter made 62 calls (one per 25 articles) using about 220k input and 24k output tokens, roughly **$0.34**, against about $0.16 for the categorize step. So the language filter is about two thirds of agent1b's ~$0.50 and the biggest single line left after the two cuts above.
- **Why it is expensive:** each call sends domain, title, an 80-word description and a 60-word excerpt per article, and asks the model to write a label for **every** article (`index` + `language` each), so output tokens (5x the price of input) are about a third of its cost, even though almost every article is plainly English.
- **Ideas, lowest risk first:**
  1. Change the answer format so the model lists only the exceptions (French and "other" articles) instead of labelling all of them. Output falls by roughly 90% and the input is untouched.
  2. Decide the obvious cases in code before calling Claude (a local language detector or stopword counts on the title, description and excerpt), and send only the uncertain articles. HN stories are overwhelmingly English and the NewsAPI French queries are overwhelmingly French, so most articles would never reach the model.
  3. Larger batches (25 -> 50) to cut the repeated instructions; a small gain, and a longer answer per call means a larger blast radius if one fails.
- **Quality gate (the owner's rule):** on a frozen pool measure two numbers against today's filter: foreign-language articles that get through (must not rise) and English or French articles wrongly dropped (must not rise). Any article the cheap path and the current filter disagree on goes to the owner to settle. Needs a labelled pool: a fresh unfiltered fetch labelled by today's filter, with every disagreement and a sample of agreements checked by hand.
- **Rollback:** a no-redeploy switch such as `AGENT1B_LANG_MODE=claude` (today's behavior stays the default and the baseline).
- **Idea 1 built (2026-10-16, PR #146, not deployed):** `AGENT1B_LANG_MODE=exceptions` (default `claude`). New tool `report_language_exceptions` (`checked` count + only the `fr`/`other` articles); unlisted articles are English. Design decision: an empty list is a valid "all English", so a failed call must not look like one. A wrong `checked`, an empty response or a cut-off answer makes that batch fall back to today's full-label call, which is stricter than the exceptions list could be on its own, and the batch is never silently read as English. Unit-tested without network.
- **Eval harness:** `python -m evals.run_lang_filter_eval` (three arms on one fresh unfiltered pool: today's filter twice as the noise floor, and exceptions mode; every article the arms disagree on goes to a rows file for the owner to settle). The pool is 1,522 articles after the prefilter (61 batches; the filter sees every fetched article, ~1,550 a week, before categorize trims to ~550).
- **Result (2026-10-16, `evals/results/lang_filter_eval_2026-10-16.json`, eval cost $0.91):**

| Arm | Input tokens | Output tokens | $ / run |
|---|---|---|---|
| today's filter | 215k | 23.4k | 0.332 |
| exceptions mode | 219k | 5.8k | 0.248 |

  That is **25% cheaper** (about $0.08 a week, ~$4 a year): input tokens dominate and exceptions mode leaves them untouched, so it was never going to save more. **Quality:** the two runs of today's filter disagree with each other on 2 of 1,522 articles (0.13%); exceptions mode disagrees with the first run on the same 2 and agrees with the second, so it is not distinguishable from today's filter against itself. Foreign-language articles let through: 1 (0.07%, same as run to run); English or French articles wrongly dropped: 0. The two articles, for the owner to settle: a 1998 ICQ security post on the Russian site opennet.ru (title only, no description; one run dropped it as foreign, the other and exceptions mode kept it as English), and a Le Monde *English-edition* article about Danish schools (labelled French by one run, English by the other and by exceptions mode; the English label looks right). No hand labels exist, so this measures consistency with today's filter, not absolute accuracy.
- **Decision (owner, 2026-10-16): enable exceptions mode; do not build the code-based language filter (idea 2).** The language filter work is therefore done after the deploy below. Exceptions mode honours each topic's allowed-language set (an English-only topic still drops French, and unlisted articles count as English only if English is allowed).
- **Deployed and switched on 2026-10-16** (owner approved): agent1b revision `agent1b-00045-w9m` with `AGENT1B_LANG_MODE=exceptions`. It was built from the then-live commit `f596a11` plus only the language-filter commit, not from `main`, because `main` also held the undeployed multi-topic engine (#143), which the owner chose to leave for later. Existing env vars and secrets untouched. Rollback with no redeploy: `gcloud run services update agent1b --update-env-vars AGENT1B_LANG_MODE=claude`. Check on the 2026-10-18 run: `(exceptions mode)` in the `[lang] Batch` log lines, no `agent1b_error`, and agent1b's LangSmith cost about $0.08 lower. The code-based language filter (idea 2) is not being built (owner decision).

Remaining Step 3 work: the language filter above, then prompt caching and the batch API, and a second measurement from the 2026-10-18 release run.

Where each original idea stands:
- **agent2b** (summarize only what can ship): done as the shortlist mode, live. Batching several articles per call was the lower-risk alternative and was not needed.
- **agent1a** (abstracts first, PDFs for the top ~8): done, live.
- **agent1b language filter**: planned, sub-step 4 above. Not started.
- **Prompt caching**: **dropped (checked 2026-10-16).** The largest static prompt or rubric in `prompts/` is 3.1 KB (about 800 tokens) and the rest are 0.5-2 KB, far under the minimum cacheable prefix (about 4,096 tokens for Haiku 4.5). What makes each call big is unique per-call text (article batches, PDFs), which cannot be cached.
- **Batch API** (50% off): **not recommended (checked 2026-10-16).** Every Claude stage (agent1a, 1b, 2a, 2b, 3) sits before the 1:15 PM Sunday draft health check, and batches are asynchronous (up to 24h), so using them would need an async hand-off between stages and moving the draft check later. For at most ~$0.50 a week (half of ~$1.05) that is a production schedule and pipeline-control change; revisit only if the owner wants it.

### Step 4: Verify prices and pick candidates
Web research done 2026-10-15 found **conflicting figures between aggregator sites**, so nothing below is a decision. Treat these as the shortlist to verify against each provider's own pricing page (which this step does, recording the date and URL for each number):

| Candidate | Reported price (in / out per M) | Notes |
|---|---|---|
| DeepSeek V4 Flash | ~$0.14-0.22 / ~$0.60-0.66 (off-peak; peak is reported ~2x; cache-hit input reported ~$0.003) | Cheapest strong general model; peak/off-peak pricing matters because the pipeline runs Sunday noon Toronto. |
| Qwen Flash tier (Alibaba DashScope) | ~$0.03-0.07 / ~$0.26-0.30 | Cheapest; capability on structured tool use must be tested. |
| GLM Flash tier (Zhipu) | ~$0.08-0.15 / ~$0.28-0.50 | Sources disagree on both numbers. |
| MiniMax M2.7 | ~$0.24 / ~$0.96 | Mid-priced. |
| DeepSeek V4 Pro | ~$0.44-0.57 / ~$0.87-1.15 | Fallback if Flash fails the quality gate. |
| Haiku 4.5 (baseline) | $1.00 / $5.00 | |
| Kimi K3, Qwen Max, GLM 5.x full | $1.3-3 / $4-15 | Not cheap enough to matter here; excluded. |

Also in scope for this step, because they decide whether a candidate is usable: does the provider support **tool use with forced or auto tool_choice** (every stage here relies on tool schemas), an OpenAI- or Anthropic-compatible endpoint, a data-retention policy we are willing to send public article text under, and hosting from a Western provider (Together, Fireworks, OpenRouter) as an alternative to the Chinese first-party API for latency and policy reasons. Also include one non-Chinese cheap baseline (Gemini Flash-Lite or similar) as a control, so "Chinese" is not conflated with "cheap".
Deliverable: a verified table in this doc, 3-4 final candidates.

### Step 5: Provider abstraction
Every stage calls `anthropic.Anthropic().messages.create(...)` directly with tools and a hard-coded `SCORING_MODEL`. Add one seam (`agents/llm.py`) that takes a stage name, resolves a `(provider, model)` from config (`MODEL_<STAGE>` env vars, defaulting to today's Haiku), and returns responses in the shape callers already consume. Keep it small: tool-use calls, usage accounting, retry. Extend `pricing.py` with the new models so cost estimates and `llm_usage` stay right. Behavior with defaults must be byte-identical (existing tests cover agent1b's graph and the fetcher; add tests for the seam).

### Step 6: Per-stage benchmark
Stages are different tasks and may pick different winners:
1. agent1b language filter + categorize (classification, tool use)
2. agent1a paper scoring (long PDF context, 8-dimension rubric; compare rank agreement with Haiku and the owner's picks)
3. agent2b news summaries (generation; the existing judge, report-only, plus owner spot labels)
4. agent3 selection / intro / dedup (existing dedup eval has an arm structure to reuse)
Each arm runs on frozen fixtures from Step 1, reports quality with intervals, cost per run from real token counts, and failure rate (tool-call schema violations, refusals, timeouts). The injection eval is re-run per candidate: a cheaper model that is easier to inject is not a win.

### Step 7: Adopt winners
Per-stage `MODEL_<STAGE>` env values on Cloud Run, rolled out one stage per week, with the health check's `llm_usage` section showing cost per agent. Keep Haiku as the documented rollback value. Add each non-Anthropic provider's key via Secret Manager like the existing ones.

### Step 8: Final re-benchmark
Re-run the Step 1 suite against the new configuration on the same fixtures plus at least 2 fresh production runs. Output: before/after cost per agent, quality deltas with intervals, failure rate, and regenerated result tables/charts via `python -m evals.make_readme_table`.

## Risks to watch

- **Aggregator prices were inconsistent**; Step 4 exists because of this. Peak/off-peak pricing on DeepSeek could erase a saving if calls land in the peak window.
- **Tool-use fidelity** is the usual place cheap models fail (malformed JSON, ignored tool_choice). Measure it explicitly.
- **Prompt injection** surface: article text is untrusted; weaker models resist worse.
- **Operational risk**: a second provider adds an outage dependency and another secret; per-stage fallback to Haiku on provider error should be part of Step 5.
- **Savings ceiling**: the run cost ~$2.06/week (~$108/year) at the baseline and ~$1.05/week after Step 3 (one debug run, 2026-10-16). What is left for Steps 4-8 is part of that $1.05: even a model 70% cheaper on every stage would save about $0.70/week, ~$36/year. Do this for the portfolio story and learning value as much as for dollars; keep each step's effort proportionate.
