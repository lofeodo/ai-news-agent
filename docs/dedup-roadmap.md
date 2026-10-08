# Article selection: caps, dedup loop, dedup eval

A living plan. Each step gets an in-depth plan under "Step detail" when we start it; when the step is done, that plan is replaced by a full write-up under "Completed steps" and the box below is ticked.

## Problem

agent3 (`agents/agent3_compose.py`, `select_articles_for_category`) picks each section's articles with one Haiku call. Two issues:

1. **Sections are too long.** The 3-5 count lives only in `prompts/article_selection_prompt.txt` ("default 3, hard max 6", and every `[NAMED RELEASE]` article is included). Nothing in code enforces any limit.
2. **Duplicates get through.** Dedup is a single prompt sentence, which fails when two outlets cover the same event. Selection runs twice per category (all-language and English-only), so both passes are affected.

## Locked decisions

| Topic | Decision |
|---|---|
| Caps | Model & Product Releases: 4. Open Source & Tools: 4. All other sections (Policy, Law & Regulation; Safety & Alignment; Industry & Business; Society & Culture; Canada & Montreal): 3. Hard caps, enforced in code. |
| Dedup mechanism | A LangGraph loop inside agent3 (ADR 0001 pattern), per section and per selection pass. One multi-turn conversation: the first turn sends every selected article; later turns send only the new fallback article and ask whether it duplicates any already-checked one. |
| Dedup model | Haiku until the eval (Step 6) picks a winner. |
| Eval arms | control (no dedup) vs Haiku vs Sonnet. |
| Failure policy | A dedup failure degrades to the undeduped selection and never fails the run. |
| Tracking doc | This file, kept up to date as steps finish. |

## Step sequence

- [ ] Step 1: Hard caps in code
- [ ] Step 2: Dedup primitive (multi-turn, tool-based)
- [ ] Step 3: Ranked fallback pool
- [ ] Step 4: LangGraph dedup loop in agent3
- [ ] Step 5: Dedup eval dataset
- [ ] Step 6: Dedup eval harness (control vs Haiku vs Sonnet)
- [ ] Step 7: Results in README and docs (later)
- [ ] Step 8: Deploy and verify on a debug run

## Steps at a glance

### Step 1: Hard caps in code
Goal: no section exceeds its cap, whatever the model returns.
Scope: per-section caps in `config.py`; truncate in code after `parse_indices`, in the model's value order, including the `[NAMED RELEASE]` path; update the selection prompt; tests.
Depends on: nothing.

### Step 2: Dedup primitive
Goal: a pure, testable function that finds duplicates among a section's selected articles and checks a single added article without resending the earlier ones.
Scope: new `agents/dedup.py`; a `report_duplicates` tool schema in the style of `filter_tool.py`; model passed as a parameter so the eval can swap it; tests with `tests/fakes.py`.
Depends on: nothing (Step 1 is preferable, so the caps are known).

### Step 3: Ranked fallback pool
Goal: when a duplicate is removed, a next-best article is available.
Scope: selection also returns ranked runners-up (today the pool is just `by_category` minus the picks); fallback is only added while the section is under its cap.
Depends on: Step 1.

### Step 4: LangGraph dedup loop in agent3
Goal: duplicates are removed and replaced, repeatedly, until the section is clean or the iteration limit is hit.
Scope: `select -> dedup_check -> (remove dups, pull fallback -> dedup_check)* -> finalize`, per section and per pass; `AGENT3_DEDUP_MODE=graph|off` rollback switch; LangSmith tracing; audit log of removed, kept and fallback articles; short ADR; tests with an injected fake client.
Depends on: Steps 2, 3.

### Step 5: Dedup eval dataset
Goal: labeled data, because the metrics mean nothing without it.
Scope: frozen fixtures of selected sets with labeled duplicate groups: real cases from past runs, synthetic near-duplicates, and hard negatives (same company, different event). Label templates in the style of `evals/make_label_templates.py`. Needs the owner to label.
Depends on: Step 2 (for the input shape).

### Step 6: Dedup eval harness
Goal: compare control vs Haiku vs Sonnet.
Scope: `evals/run_dedup_eval.py` and `evals/dedup_eval.py` following `run_review_eval.py`: `--dry-run` and `--approve`, `CostGuard`, models from `config.py`. Metrics with Wilson intervals (`evals/stats.py`): duplicate recall, false-removal rate, residual duplicate rate, cost, latency. Results JSON in `evals/results/`.
Depends on: Steps 2, 5.

### Step 7: Results in README and docs (later)
Goal: publish the eval results.
Scope: marker block in `docs/evaluation.md`, renderer in `evals/make_readme_table.py`, chart in `evals/make_charts.py`.
Depends on: Step 6.

### Step 8: Deploy and verify
Goal: confirm it works on a real run.
Scope: rebuild agent3, force the `create-newsletter-debug` job, check that caps hold, no duplicates remain, the audit log is sensible and the cost is acceptable. Pick the production dedup model from Step 6.
Depends on: Steps 1 to 6.

## Step detail

### Step 1 plan: hard caps in code (branch `feat/section-caps`)

Current behavior (`agents/agent3_compose.py`): `select_articles_for_category` asks the model for a JSON array of indices, `parse_indices` filters out-of-range values, and the result is used as is. If parsing yields nothing, it falls back to the first 3 articles. The prompt (`prompts/article_selection_prompt.txt`) says default 3, hard max 6, and "include every [NAMED RELEASE] article (may reach 5 or more)". `ARTICLES_PER_CATEGORY_TARGET` (L23) is defined but unused.

Design:
1. `config.py`: add `SECTION_CAP_DEFAULT = 3` and `SECTION_CAPS = {"Model & Product Releases": 4, "Open Source & Tools": 4}`, plus `section_cap(category) -> int`. Keeping the caps in `config.py` makes them reachable by later steps (dedup loop, eval) without importing agent3.
2. `agent3_compose.py`, in `select_articles_for_category`, after `parse_indices`: drop repeated indices (the model could return `[2, 2, 5]`, which would show the same article twice), then truncate to `section_cap(category)`. The model is asked for most valuable first, so truncation keeps the best. The empty-result fallback becomes `range(min(cap, len(ordered)))`.
3. `prompts/article_selection_prompt.txt`: add a `{cap}` placeholder (the code passes it to `.format`; test templates like `"{category}{articles}"` ignore extra keys, so existing tests still work). Replace the "default 3 / hard max 6" rule with "pick at most {cap}; fewer is fine; do not pad". Reword the named-release paragraph: prefer the named releases, at least one per lab where the cap allows, never above {cap}. Keep the rest.
4. Remove the dead `ARTICLES_PER_CATEGORY_TARGET`.
5. Tests in `tests/test_selection_caps.py` using the fakes: model returns 6 indices for a 3-cap section (keeps the first 3), 6 for a 4-cap section (keeps 4), duplicated indices, out-of-range indices, empty response fallback respects the cap, `section_cap` values for all 7 categories, and a prompt check that `{cap}` is rendered.
6. Run `venv\Scripts\python -m pytest -q`.
7. Commit in small units: config + helper, enforcement + tests, prompt. Update this doc (move Step 1 to "Completed steps", tick the box) in the last commit.

Verification: pytest green; a local run of `selection_test.py` is a manual script that makes real Claude calls, so it is only run if the owner asks.

Open question for the owner: should the English-only pass use the same caps? Assumed yes (same function).

## Completed steps

None yet.
