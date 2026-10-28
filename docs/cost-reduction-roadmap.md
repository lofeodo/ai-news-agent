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
- [x] Step 4: Verify model prices and pick candidates (DeepSeek V4.1 Flash verified; GLM and Kimi not tested, no API keys yet)
- [x] Step 5: Provider abstraction (`agents/llm.py`, Claude stays the default)
- [x] Step 6: Per-stage model benchmark (language filter and categorize pass; summaries held for injection; papers fail)
- [x] Step 7: Adopt the winners per stage behind per-stage env switches (agent1b on DeepSeek since 2026-10-28, `agent1b-00046-ddh`)
- [x] Step 8: Final re-benchmark against the Step 1 baseline ($2.06 -> $0.55, one production debug run; the release runs confirm)

**Status (2026-10-28): the roadmap is complete.** Weekly cost **$2.06 -> $0.55 (-73%)**, measured on a production debug run with everything on: review off, agent1a abstract-first, agent2b shortlist, language filter in exceptions mode, and agent1b's two Claude stages moved to DeepSeek V4.1 Flash (the 2026-10-16 debug run on the same code minus DeepSeek cost $1.05). Still open, none of it blocking: news summaries on DeepSeek (held for the title-echo injection result), GLM / Kimi / Qwen Flash as further candidates (no keys yet), and confirming the figure on the next two Sunday release runs (the first is 2026-11-01).

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

### Step 4: Verify prices and pick candidates (done 2026-10-16)
**Owner's direction: test Chinese models** (DeepSeek, GLM, Kimi at minimum). Aggregator sites disagreed with each other, so prices were taken from the provider's own page: `https://api-docs.deepseek.com/quick_start/pricing`, read 2026-10-16.

| Model | API id | Input (cache miss) / output per M, off-peak | Peak (2x) | Context |
|---|---|---|---|---|
| **DeepSeek V4.1 Flash** (launched 2026-09-10; the legacy `deepseek-v4-flash` id is served by it) | `deepseek-flash` | $0.15 / $0.60 (cache hit $0.003) | $0.30 / $1.20 | 1M |
| DeepSeek V4 Pro | `deepseek-v4-pro` | $0.66 / $1.98 | $1.32 / $3.96 | 1M |
| Haiku 4.5 (baseline) | | $1.00 / $5.00 | | |

- **Peak window:** 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday (not Chinese public holidays). The Sunday 12:00 PM Toronto pipeline run (16:00-17:00 UTC) is off-peak; a weekday-morning debug run can land in peak at double the price.
- **Tool use works** through DeepSeek's Anthropic-compatible endpoint (`https://api.deepseek.com/anthropic`), tested with real calls: `tool_choice` auto, `any` and a forced named tool all return a valid tool call. **It thinks by default, which bills extra output tokens (201 vs 82 in the probe) and makes it reject a forced `tool_choice` (HTTP 400 "Thinking mode does not support this tool_choice")**, so every call sends `thinking: {"type": "disabled"}`.
- **GLM and Kimi were NOT benchmarked, against the owner's "at the bare minimum try those":** the only key available was DeepSeek's. Aggregator-reported prices (unverified): GLM-5.2 about $1.40 / $4.40, Kimi K3 about $3 / $15 and K2.6 about $0.95 / $4, which are Haiku's price class or above, and the GLM Flash tiers about $0.06-0.15 in. A GLM Flash tier and Kimi K2.6 are the ones worth testing. Each needs an API key and its provider's Anthropic-compatible base URL added to `agents/llm.py` (a small change, the evals are model-agnostic). Qwen Flash ($0.03 in, one aggregator) is the other open follow-up.
- **Not done:** data-retention policy of DeepSeek's first-party API was not reviewed. Only public article text is sent (see Principles); the owner should confirm that is acceptable before the language filter and categorize are switched to it.

### Step 5: Provider abstraction (done 2026-10-16)
`agents/llm.py` is the single seam. Every stage asks `llm.model_for("<stage>")` for its model and builds its client with `llm.make_client(agent, run_id)`. Stages: `lang_filter`, `categorize` (agent1b), `paper_score` (agent1a), `news_summary` (agent2b), `paper_summary` (agent2a), `select` (agent3 selection and intro). agent3's duplicate removal is not routed (about $0.01 a run).
- **Default is byte-identical:** with no `MODEL_<STAGE>` variable set, `model_for` returns the Haiku model and `make_client` returns exactly what `tracing.make_client` did. A stage set to a `deepseek-*` id (e.g. `MODEL_LANG_FILTER=deepseek-flash`) goes to DeepSeek's endpoint with thinking off; the other stages are untouched. Rollback is unsetting the variable (no redeploy of code).
- **Fallback:** a DeepSeek error other than a rate limit retries that one call on Haiku, logs `[llm] DeepSeek call fell back to Claude`, and never fails the run; a missing `DEEPSEEK_API_KEY` does the same. A rate limit propagates so each caller's own retry loop handles it.
- **Tracing and prices:** the DeepSeek client is wrapped for LangSmith like the Claude one (`tracing.wrap_for_tracing`); `agents/pricing.py` has DeepSeek's off-peak and `@peak` list prices.
- 9 unit tests in `tests/test_llm.py` (routing, thinking off, fallback, rate-limit propagation, prices); the full suite passes.

### Step 6: Per-stage benchmark (done 2026-10-16)
Same frozen inputs as Step 1, production functions, `MODEL_<STAGE>` set per arm. Runners: `evals/run_lang_model_eval.py`, `evals/run_model_stages_eval.py` (categorize, summaries, papers), `evals/run_injection_eval.py --model deepseek`. Total spend on the evals: about $2 (mostly Haiku reference arms and the Sonnet judge; DeepSeek about $0.15), including one summaries run that crashed on a judge-parsing bug and was rerun. Results in `evals/results/*_model_eval_2026-10-16.json`. DeepSeek costs below are cold-cache, off-peak; a rerun of identical prompts is served from DeepSeek's cache and understates cost.

| Stage | Haiku | DeepSeek V4.1 Flash | Verdict |
|---|---|---|---|
| **Language filter** (1,522 articles, exceptions mode) | $0.248 | **$0.024** | **Adopt.** 4 articles differ between any arms, 3 of them noise-level; Haiku's own repeat runs differ on 2. DeepSeek read two English-edition articles (Le Monde, Folha) as English and dropped a Spanish one that Haiku's full-label run mislabelled French. Foreign articles let through: 1 (0.07%), same as run to run. 0 fallbacks, 0 failed batches. |
| **Categorize** (500 articles, 100 gold labels) | accuracy 0.53 [0.43-0.63], $0.083 | 0.63 [0.53-0.72] and 0.56 [0.46-0.65] on two runs, **$0.0096** | **Adopt.** Not distinguishable from Haiku (intervals overlap; the point estimate is not lower); 84% agreement with Haiku, 89% with itself. Accuracy counts articles the model skipped as wrong (coverage 0.92 Haiku, 0.96 and 0.92 DeepSeek). |
| **News summaries** (60 articles) | $0.099 | **$0.012** | **Do not adopt yet (injection).** Quality reads as good or better (sampled by hand: tighter, follows "1-2 sentences" better, more specific; mean 45 words against 59), 60/60 produced. But on the injection eval DeepSeek echoed a planted string from an article **title** at the start of the summary in 5 of 5 repeats of one case, plus 1 markup case: 6/20 attacks succeeded [15%-52%] against Haiku's 0/20. That text would reach subscribers. The pairwise Sonnet judge returned no verdict on 68% of items and is not usable. |
| **Paper scoring** (35 papers, full text, 2 passes) | self-agreement Spearman 0.77, top-8 overlap 0.62 | self-agreement **0.58**, agreement with Haiku **0.36**, top-8 overlap with Haiku 0.50, 1 of Haiku's top 3 inside its top 5 | **Do not adopt.** It is noisier than Haiku and ranks differently; the saving was at most about $0.20 a week. |
| Categorize injection | 0/20 [0-16%] | 1/20 [1-24%] (one forced-category breakout) | Not distinguishable at this size; accepted with the categorize result. |

Language-filter prompts are not in the injection cases: the filter's only output is a language label, so the worst case is one foreign article getting through, which the 0.07% rate bounds.

### Step 7: Adopt winners (deployed 2026-10-28)
agent1b runs on revision **`agent1b-00046-ddh`** (image `agent1b:deepseek-be609d1`) with `MODEL_LANG_FILTER=deepseek-flash`, `MODEL_CATEGORIZE=deepseek-flash` and the `DEEPSEEK_API_KEY` secret (`deepseek-api-key`, readable by agent1b's service account); every other variable is unchanged. The image was built from the then-live commit `f596a11` plus the language-filter commit plus an agent1b-only copy of the seam (branch `deploy/agent1b-deepseek`, `be609d1`), not from `main`, because `main` also holds the undeployed multi-topic engine (#143) and the editions renderer (#151). The owner ran the secret and service commands by hand (agent deploys are permission-blocked). agent1a, agent2a, agent2b and agent3 stay on Haiku. **Rollback with no redeploy:** `gcloud run services update agent1b --update-env-vars MODEL_LANG_FILTER=,MODEL_CATEGORIZE=`; a DeepSeek error already falls back to Haiku per call.
- **First run on it (debug, 2026-10-28, run `2026-10-28T134338Z`):** 1,551 articles after the prefilter, the language filter dropped 123 (every batch in exceptions mode), categorize selected 517 (the Haiku runs selected about 500-550), no `[llm] ... fell back to Claude` line, no `*_error`, no `agent1b_warning`, composed in about 5 minutes end to end.
- **News summaries stay on Haiku until the title echo is fixed.** A fix to try is a code check that rejects a summary starting with text that appears in the title but not in the article body, or a stronger guard for the DeepSeek arm, then re-run `python -m evals.run_injection_eval --model deepseek --agents summarize_2b` (about $0.01) and the summaries eval. Worth about $0.23 a week.
- **Reporting:** LangSmith has no price for `deepseek-flash`, so `agents/usage_archive.py` now prices such calls from `agents/pricing.py` by the recorded model name (`calls_priced_locally` on the usage record) and `python -m evals.fetch_pipeline_cost --run <id>` measures an unarchived run straight from LangSmith. The healthcheck service must be redeployed from `main` to pick up the archiver change, otherwise the Sunday release run's DeepSeek calls are archived with no cost (and `fetch_pipeline_cost` skips that run); `agents/drift.py`'s separate "list-price estimate" line still prices every token at the Haiku rate (overstates agent1b by about 10x; cosmetic).

### Step 8: Final re-benchmark (done 2026-10-28, one run; release runs to confirm)
`evals/results/pipeline_cost.json` (git on this branch), from `python -m evals.fetch_pipeline_cost --run 2026-10-28T134338Z`, LangSmith token counts, Claude at LangSmith's price and DeepSeek at the off-peak list price:

| Agent | Baseline $ (Step 1, mean of 4) | 2026-10-16 $ (Step 3 live) | 2026-10-28 $ (all steps) | Calls now | What changed |
|---|---|---|---|---|---|
| agent1a | 0.467 | 0.238 | **0.202** | 36 | abstract-first (8 of 28 papers read in full) |
| agent1b | 0.637 | 0.498 | **0.050** | 78 | review off, exceptions mode, both stages on DeepSeek |
| agent2a | 0.014 | 0.011 | **0.010** | 1 | unchanged |
| agent2b | 0.805 | 0.263 | **0.248** | 84 | shortlist (71 of 517 summarized) |
| agent3 | 0.132 | 0.041 | **0.040** | 17 | smaller pool to choose from |
| **Total** | **2.055** | **1.051** | **0.550** | 216 | **-73%** (about $78 a year) |

- The README's cost section and `docs/assets/chart-cost.svg` were regenerated from this file (`python -m evals.make_readme_table`); the chart says "1 archived run" until the release runs are added.
- Quality on this run was checked from the run doc and logs only (counts, no errors); the per-stage quality evidence is Step 6's benchmark on frozen inputs. The owner should eyeball the 2026-11-01 issue (the first release run on DeepSeek) and the Monday health check, and after two release runs re-run `python -m evals.fetch_pipeline_cost --run <id> <id>` to replace the single-run figure.
- DeepSeek's own usage page is the invoice-side check of the $0.05; it was not read for this run.

## Risks to watch

- **Aggregator prices were inconsistent**; Step 4 took them from DeepSeek's own page. Peak pricing is double: keep scheduled runs off-peak (the Sunday run is).
- **Tool-use fidelity** held up on DeepSeek (0 failed batches and 0 fallbacks to Haiku over about 450 DeepSeek calls) once thinking is off; with thinking on, forced tool choice is rejected.
- **Prompt injection** surface: article text is untrusted, and a cheaper model was measurably easier to inject on summaries (see Step 6). Re-run the injection eval for any stage before it moves.
- **Operational risk**: a second provider adds an outage dependency and another secret. The per-call fallback to Haiku covers outages; a missing key also falls back.
- **Data policy:** DeepSeek's first-party API retention terms were not reviewed (Step 4). Only public article text is sent.
- **Savings ceiling**: the run cost ~$2.06/week (~$108/year) at the baseline and ~$1.05/week after Step 3. With the language filter and categorize on DeepSeek it projects to about $0.60. What is left is agent1a ($0.24, stays on Haiku), agent2b ($0.26, held back) and small items.
