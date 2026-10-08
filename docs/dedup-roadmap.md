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

- [x] Step 1: Hard caps in code
- [x] Step 2: Dedup primitive (multi-turn, tool-based)
- [x] Step 3: Ranked fallback pool
- [x] Step 4: LangGraph dedup loop in agent3
- [x] Step 5: Dedup eval dataset
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

(No step in progress. Step 6 gets its plan here when we start it.)

## Completed steps

### Step 1: Hard caps in code

Branch `feat/dedup-roadmap` (the work stayed on the branch that holds this doc rather than a separate `feat/section-caps`). Four commits.

**Built.**
- `config.py`: `SECTION_CAP_DEFAULT = 3`, `SECTION_CAPS` (Model & Product Releases 4, Open Source & Tools 4) and `section_cap(category)`. The caps live in config so the later dedup loop and the eval can read them without importing agent3.
- `agents/agent3_compose.py`, `select_articles_for_category`: after `parse_indices`, repeated indices are collapsed (a model reply like `[2, 2, 5]` would otherwise show one article twice), then the list is truncated to the section's cap. The model is asked for most valuable first, so truncation keeps its best picks; a log line records when a cap trimmed a reply. The empty-reply fallback now takes the first `cap` articles instead of a hard-coded 3. The unused `ARTICLES_PER_CATEGORY_TARGET` constant was removed.
- `prompts/article_selection_prompt.txt`: a `{cap}` placeholder, filled by the code. The old "default 3, go above 3 for major stories, hard max 6" rule and "include every [NAMED RELEASE] article (may reach 5 or more)" are gone. The prompt now says "at most {cap}", fewer is fine, and named releases are preferred (at least one per lab where the cap allows) but never push the section past the cap.
- `tests/test_selection_caps.py` (19 tests): over-cap truncation for 3-cap and 4-cap sections, truncation keeps the model's first picks in order, under-cap replies untouched, repeated and out-of-range indices, repeats not eating the cap, empty-reply fallback respects the cap and the pool size, `section_cap` for all 7 categories and an unknown one, and the real prompt file renders the right cap.

**Verified.** `venv\Scripts\python -m pytest -q`: 405 passed. No real Claude calls were made.

**Caveats.**
- Caps are enforced in code, so they hold even if the prompt is ignored. They apply to both selection passes (all-language and English-only), since both call the same function.
- When a section has more `[NAMED RELEASE]` articles than its cap, the model's ordering decides which survive; there is no code-level guarantee of one per lab any more.
- Nothing changes in production until agent3 is rebuilt and deployed (Step 8). Existing runs and stored newsletters are unaffected.

### Step 2: Dedup primitive

Branch `feat/dedup-roadmap`. Commits: plan, tool schema/config/guard, primitive with tests, this write-up.

**Built.**
- `agents/dedup.py`: `DedupConversation(client, category="", model=DEDUP_MODEL, create=None)`. `start(articles)` sends every article once and returns duplicate groups (indices 0..n-1); `add(article)` appends one new article as a new user turn (its `tool_result` for the previous call first, then the candidate text) and returns the `DuplicateGroup` it belongs to, or None. Numbering is stable across turns. `usage` accumulates input/output tokens. `create` defaults to `client.messages.create`, so Step 4 can inject agent3's retry wrapper; the module imports nothing from agent3. It only reports groups; which member to keep is Step 4's policy.
- `agents/dedup_tool.py`: `report_duplicates` tool (`groups: [{indices, reason}]`).
- `prompts/dedup_prompt.txt`, `prompts/dedup_candidate_prompt.txt`: same event counts as duplicate even across outlets, languages and angles; same company or topic alone does not; when unsure, don't group.
- `config.py`: `DEDUP_MODEL = SCORING_MODEL` (Haiku), `DEDUP_MAX_TOKENS = 1000`. `prompt_guard.py`: `GUARD_DEDUP`. `tests/fakes.py`: `ScriptedClient`.
- Output validation (`clean_groups`): non-int, bool and out-of-range indices dropped, repeats collapsed, groups under 2 members dropped, overlapping groups merged (first reason kept); on `add`, only the group containing the new article counts.
- `tool_choice` is `auto` (Sonnet 5.5 rejects forced tools); a reply without the tool call is retried once, then `DedupError`. API errors also raise `DedupError`. Nothing is swallowed here; Step 4 degrades to the undeduped selection.

**Verified.** `tests/test_dedup.py` (14 tests): group found, empty cases, prompt content, only the new article sent on `add` with history and tool_result order intact, stable numbering, groups not containing the new article ignored, `add` before `start`, validation rules, retry then `DedupError`, retry then success, API error, model and `tool_choice` passed through, injected tags neutralized, usage and injected `create`. Full suite: 419 passed. No real Claude calls.

**Caveats.**
- The prompts have never been sent to a real model; quality is unmeasured until the Step 6 eval.
- Removed articles stay in the conversation context, so a candidate could be flagged against a removed article's number; Step 4 must map such a match back to the kept member of that group.
- Nothing in agent3 uses this yet, so production is unchanged.

### Step 3: Ranked fallback pool

Branch `feat/dedup-fallback-pool`. Commits: plan, implementation with tests, this write-up.

**Built.**
- `prompts/article_selection_prompt.txt`: the reply is now `{"selected": [...], "runners_up": [...]}`, with a runners-up paragraph (same standards, no filler, different stories from the picks, most valuable first). `config.py`: `RUNNERS_UP_MAX = 6`.
- `agents/agent3_compose.py`: `parse_selection` (object or bare array; repeats, out-of-range, bools and runners-up that are also picks dropped), `SelectionResult(picks, runners_up)`, and `select_with_runners_up`, which holds the old selection logic unchanged (blank-card filter, neutral shuffle, cap, empty-reply fallback). `select_articles_for_category` is now a wrapper returning `.picks`, so no existing caller changed.
- Pool order: picks the cap trimmed (the model chose them), then the model's runners-up, then a deterministic top-up to 6 by HN score descending (unscored last), ties in shuffled order. The pool is drawn only from the section's own articles; each selection pass (all-language, English-only) builds its own.
- `take_fallback(category, picks, runners_up, used=())`: next runner-up not already kept or used, or None when the kept picks already reach the section's cap. Changed from the planned `removed` argument: the caller passes the kept list and everything already tried, which is what Step 4's loop has.

**Verified.** `tests/test_selection_pool.py` (17 tests); full suite 436 passed, no real Claude calls.

**Caveats.**
- The new reply format has never been sent to a real model; runners-up quality is unmeasured until the Step 6 eval. A model that ignores the format and returns a bare array still works (the top-up supplies the pool).
- agent3 does not use the pool yet, so production output is unchanged. The one visible difference is a longer selection reply (slightly more output tokens per call).
- The English-only pass can reuse a different pool than the all-language pass, by design.

### Step 4: LangGraph dedup loop in agent3

Branch `feat/dedup-graph`. Commits: plan, graph module with tests, agent3 wiring with tests, this write-up and ADR 0002.

**Built.**
- `agents/agent3_dedup_graph.py`: `dedup_section(category, picks, runners_up, client, create, cfg, run_id, pass_name) -> DedupResult(picks, audit, usage)`, a per-section LangGraph `check_start -> resolve -> refill* -> finalize`. Keep policy: highest HN score, ties to the earliest pick. Refill takes one runner-up per visit via `take_fallback`, checks it with `DedupConversation.add`, and stops when the section is back to its original size, the pool is empty or `max_iterations` is reached (it never pads a section the model left short). A fallback flagged against a removed article is redirected to the kept member and dropped (the Step 2 caveat).
- Any exception degrades to the original picks with audit status `degraded`; `dedup_section` never raises. Fewer than 2 picks skips the call (`skipped`).
- `config.py`: `AGENT3_DEDUP_MODE` (`graph` default, `off` rollback, validated at import) and `DEDUP_MAX_ITERATIONS` (default 6).
- `agents/agent3_compose.py`: `run()` uses `select_with_runners_up` then dedups both passes; when a category has no French articles the English pass reuses the deduped result. `claude_call_with_retry` is injected as the dedup `create`. `write_dedup_audit` writes `data/agent3_dedup_log.json` and, in cloud mode, Firestore `agent3_audits/{run_id}`; `build_run_doc_update` adds a counts-only `agent3_dedup_summary`.
- LangSmith: run name `agent3_dedup`, tags `agent3`, `dedup`, metadata run id, category and pass.

**Verified.** `tests/test_dedup_graph.py` (12) and `tests/test_agent3_dedup_integration.py` (3, `run()` end to end with a routing fake). Full suite 451 passed, no real Claude calls.

**Caveats.**
- Prompts and the loop have never run against a real model; dedup quality and cost are unmeasured until Steps 5-6.
- Production is unchanged until agent3 is rebuilt and deployed (Step 8). The first deploy should be checked on a debug run (`agent3_dedup_summary`, `agent3_audits/{run_id}`).
- A fallback that duplicates a kept article is simply dropped; a higher-HN fallback does not replace the kept one.
- The extra `agent3_audits` Firestore collection has no TTL rule.

### Step 5: Dedup eval dataset

Branch `feat/dedup-eval-dataset`. No Claude calls.

**Built.**
- `evals/make_dedup_cases.py`: seeded builder. It mines the 325-article pool in `data/news_summaries.json` for suspected duplicate pairs by word overlap within a category, then builds cases of 8 articles (the pair plus same-category fillers that overlap with nothing). Kinds: `real` (high overlap), `hard_negative` (medium overlap), `control` (no similar pair), `synthetic` (hand-written rewrites from an optional `evals/fixtures/dedup_synthetic.json`, unused so far). Reposts are excluded: pairs with near-identical titles (ratio 0.85) or on the same site are skipped, because a biztoc.com copy of one article is not the cross-outlet case the dedup step exists for.
- `evals/fixtures/dedup_cases.json` (tracked: ids, titles, 300-char snippets, summaries; no full text) and `evals/labels/dedup_cases_template.csv` (written with a byte-order mark so Excel reads accents; gold column always empty when generated).
- `evals/fetch_shipped_picks.py` (read-only Firestore): for each composed `pipeline_runs` doc, reads the article links inside every `SECTION` block of the 1_1 and 0_0 variants and matches them to the run's stored pool by url (17 runs, June to October 2026, 119 sections). The union per section is what agent3 picked before duplicate removal existed. Output is `data/shipped_picks.json` (gitignored: third-party text).
- `python -m evals.make_dedup_cases shipped`: one case per shipped section (3+ articles): `shipped` (a pair with word overlap >= 0.2, reposts ignored; 30 cases) and `shipped_control` (no such pair; 10 sampled so the gold is not conditioned only on what the matcher flags). Sections sharing an article with the first set are skipped. Files: `evals/fixtures/dedup_cases_shipped.json` and `evals/labels/dedup_cases_shipped_template.csv`. Regenerating refuses to overwrite a label file that already has labels.
- `evals/dedup_labels.py`: `load_dedup_gold()` returns `Gold(groups, excluded, borderline, unlabeled_cases)` and `load_all_gold()` merges both label sets; both validate case and article ids and reject one-member groups. They read files saved by Excel (BOM, and restore the leading zero Excel drops from all-digit ids such as `051952554936`).
- Labeling rule, same as `prompts/dedup_prompt.txt`: the same specific story is a duplicate, even across outlets and languages and even with a different focus or opinion; same company or broad topic alone is not. `t` in `note` marks same topic, different angle (borderline; `Gold.borderline`, kept out of the strict gold). The owner labeled by hand.
- Rule change (2026-10-08): the first label file (17 cases) was labeled under "same event" before the owner broadened it to "same story, any angle". Pairs marked stay valid; unmarked articles in the `hard_negative` cases may need a second look. The prompts were updated to match (nothing deployed yet).

**Result.** 57 cases, 361 articles: the mined-pool set (17 cases: 2 real, 10 hard_negative, 5 control) and the shipped-section set (40 cases: 30 shipped, 10 shipped_control). Gold: 29 duplicate groups in 27 cases (27 pairs, 2 groups of three; 60 articles), 10 borderline `t` articles, no articles marked ambiguous, 26 cases with no duplicates. By set: 8 groups in the first set, 21 in the shipped set (including 3 in the random `shipped_control` cases, which shows the word-overlap matcher misses real duplicates). Tests: `tests/test_make_dedup_cases.py`, `tests/test_dedup_labels.py`; full suite 469 passed.

The shipped set shows that real duplicates reached readers before this step existed (for example one OpenAI incident covered by three outlets in a single section).

**Caveats.**
- 29 positive groups is still small. Wilson intervals on duplicate recall stay wide (for example 26 of 29 is about 74% to 97%), so Step 6 separates models only if the gap is large. Duplicate-group counts per kind are uneven: only 2 groups in `real` cases.
- The `kind` labels of the first set are the builder's guess, not truth: several `hard_negative` cases contained real duplicates.
- Candidate pairs were found by word overlap, so duplicates worded very differently were underrepresented in the first set; the shipped set covers whole sections, so it is less affected, though the 30 `shipped` cases were still picked by the 0.2 overlap rule. There are no synthetic cases.
- Judgment calls: stories about one product launch with a different focus (an Opus 5.5 guide, a head-to-head, a science result) are left as separate stories, not duplicates. The `t` marker is for same-topic borderline pairs; Step 6 can report strict and broad results separately.
- Only the first-turn check (`start`) is directly scored. The fallback path (`add`) can be scored by holding articles out of these cases.
- Cases were built from a single week's pool.

