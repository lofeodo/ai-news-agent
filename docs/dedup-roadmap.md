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

### Step 2: Dedup primitive (in progress)

**Goal.** `agents/dedup.py`: one multi-turn conversation per (section, selection pass). Turn 1 sends all selected articles and gets duplicate groups back. Each later turn sends one new article and gets back which earlier articles it duplicates. Nothing in agent3 changes in this step, so production behavior is unchanged.

**Design.**
- `config.py`: `DEDUP_MODEL = SCORING_MODEL` (Haiku until the Step 6 eval). The model is also a constructor parameter so the eval can pass Sonnet.
- Tool schema in `agents/dedup_tool.py` (style of `filter_tool.py`): `report_duplicates` with `groups: [{indices: [int], reason: str}]`. A group is two or more articles reporting the same underlying event or announcement (different outlets, languages or angles still count). An empty `groups` means no duplicates. Same company or same topic with a different event is *not* a duplicate.
- `prompts/dedup_prompt.txt` (turn 1) and `prompts/dedup_candidate_prompt.txt` (later turns), plus `GUARD_DEDUP` in `prompt_guard.py`. Articles use the same `<article_N>` format and `neutralize_tags` as selection (title plus first 300 chars of summary/description).
- API (no agent3 imports, so Step 4 can import it without a cycle):
  - `DuplicateGroup(indices: tuple[int, ...], reason: str)`
  - `DedupConversation(client, model=DEDUP_MODEL, create=None)`; `create` defaults to `client.messages.create` so Step 4 can inject agent3's `claude_call_with_retry` wrapper.
  - `.start(articles) -> list[DuplicateGroup]` numbers articles 0..n-1.
  - `.add(article) -> DuplicateGroup | None` numbers it n, n+1, ...; returns the group (earlier indices plus the new one) or None. Numbering is stable for the whole conversation, so Step 4 maps indices back to its own list; removed articles stay in the conversation context.
  - `.usage` (input/output tokens) for cost reporting in the eval.
  - Which member of a group to keep is not decided here (Step 4's policy).
- Message shape: user (articles) -> assistant `tool_use` -> user `[tool_result "recorded", then candidate text]` -> ... The tool_result block comes first in the user content.
- `tool_choice` stays `auto`, with the prompt saying to always call the tool: `claude-sonnet-5-5` returns a 400 on a forced tool (see the judge note in CLAUDE.md) and the eval needs a Sonnet arm. If no tool call comes back, retry once, then raise.
- Model output is validated, never trusted: drop non-int and out-of-range indices, dedupe within a group, drop groups under 2 members, merge groups that share an index; on a candidate turn keep only the group containing the new index.
- Failure: raises `DedupError` (API error, no tool call twice, malformed input). The primitive does not swallow it; Step 4 catches it and degrades to the undeduped selection, per the locked failure policy.

**Files.** New: `agents/dedup.py`, `agents/dedup_tool.py`, the two prompts, `tests/test_dedup.py`. Edited: `config.py`, `agents/prompt_guard.py`, `tests/fakes.py` (a `ScriptedClient` returning queued responses and recording each call's `messages`/`model`).

**Tests** (no network): group found on turn 1; empty groups; `add` sends only the new article (earlier titles absent from the new turn, history preserved, tool_result first); `add` match / None; stable numbering across several `add`s; invalid indices, singleton and overlapping groups; no tool call -> one retry then `DedupError`; API exception -> `DedupError`; `model` param reaches the call; `tool_choice` is `auto`; injected tags in a title are neutralized; real prompt files render; usage accumulates. Full suite stays green (405 passed before this step).

**Out of scope.** Wiring into agent3, fallback pool, LangGraph, audit log, eval data. A real-model smoke check belongs to Step 6.

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
