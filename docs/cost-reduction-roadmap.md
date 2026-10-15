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
- [ ] Step 3: Cut call volume (agent2b batching, agent1a pre-ranking, prompt caching)
- [ ] Step 4: Verify model prices and pick candidates (research, no code)
- [ ] Step 5: Provider abstraction (one `llm` client seam, Claude stays the default)
- [ ] Step 6: Per-stage model benchmark (cheap models vs Haiku baseline)
- [ ] Step 7: Adopt the winners per stage behind per-stage env switches
- [ ] Step 8: Final re-benchmark against the Step 1 baseline

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

**Gap found:** there are no frozen fixtures or labels for **agent1a paper scoring** or **agent2b/2a summaries**, and those are exactly the stages Step 3 changes. Building them (a frozen article set and a frozen paper set with Haiku's current outputs as the reference, plus rank agreement and the report-only judge as metrics) is the first task of Step 3, before any change to those agents, so the "before" is captured on the unmodified code.

### Step 2: Turn off the review loop (done 2026-10-15)
The review eval says it plainly: confidence does not predict errors (Spearman 0.117, intervals overlap), and the loop's final accuracy (0.600) is not distinguishable from the single pass (0.622) or from its own first pass (0.589).
- **Shipped:** `ReviewConfig.from_env()` now defaults `REVIEW_MAX_ARTICLES` to 0 (`DEFAULT_REVIEW_MAX_ARTICLES` in `agents/agent1b_graph.py`), so the route node sends nothing to `review`. The graph nodes, the audit and the eval are untouched and reproducible. Rollback with no redeploy: `gcloud run services update agent1b --update-env-vars REVIEW_MAX_ARTICLES=30`. Takes effect when agent1b is next deployed.
- **Expected saving:** ~$0.03-0.05 a run (0-30 extra calls and fetches), to be confirmed from the first run's LangSmith cost, not measured yet.
- **Not done (gated):** dropping the `confidence` field from the categorize tool schema. The owner's condition is a categorize accuracy check against the Step 1 baseline, which needs real Claude calls (`python -m evals.run_review_eval`, first-pass arm only), so it is left for an explicit go-ahead. Until then the confidence addendum stays in the prompt and costs a few output tokens per article.

### Step 3: Cut call volume
Independent of model choice, and compounding with it:
- **agent2b**: ~490 calls for ~15-25 shipped articles. Summarize only what agent3 can actually ship (the post-selection pool plus runners-up), or batch several articles per call. Needs an ordering check: agent2b currently runs in parallel with agent2a before agent3 selection, so this may mean summarizing only the top-N per category by HN points.
- **agent1a**: 35 full-PDF scoring calls (~12k tokens each). Score abstracts first, send PDFs for the top ~8 only.
- **Prompt caching** for the large shared system prompts and rubrics (Anthropic cache reads are 0.1x).
- **Batch API** (50% off) where latency allows; the pipeline has a ~19h window between Sunday noon and Monday 7 AM, so most stages could run as a batch. Only worth it if the stage is not on the critical path for the draft health check at 1:15 PM, which needs to be checked.

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
- **Savings ceiling**: total is ~$2/week, ~$108/year. Do this for the portfolio story and learning value as much as for dollars; keep each step's effort proportionate.
