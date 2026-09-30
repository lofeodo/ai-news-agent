# Plan: LangGraph inside Agent 1b

(On approval, first action = create branch `feat/langgraph-agent1b` and copy this file to `docs/plans/langgraph-agent1b.md`. Plan mode only lets me write here.)

## Context
Agent 1b's fetch → filter → categorize is a linear script. We want a real graph *inside* the agent (explicit nodes, a conditional edge, a ReAct tool loop) that runs identically in local and cloud mode, without touching the Pub/Sub + Firestore-counter layer between agents. Evaluation comes later, so the old single-pass path stays callable and every decision is logged per article.

## Phase 0 findings — where the code differs from the brief
1. **Batch size is 100, not 200** (`FILTER_BATCH_SIZE = 100`, `agent1b_fetch_news.py:352`).
2. **There is a second Claude stage the brief omits:** `language_filter()` (batches of 25, tool `filter_by_language`) runs *between* prefilter and categorize. It becomes its own node; unchanged.
3. **The categorize tool returns only *selected* articles** (`filter_articles` = relevance filter + category). Confidence therefore exists only for kept articles; dropped ones are never reviewed.
4. **The fetch helper lives in `agent2b_summarize_news.py`**, which imports `newspaper` at module top and owns `_parse_lock` (part of the 2026-09-28 SIGABRT mitigation). Also: `fetch_article_text` swallows all exceptions and returns `None` for both "error" and "<100 words", so failure vs. thin content is indistinguishable. Extraction must keep agent2b behavior identical.
5. **`requirements.txt` is only partly pinned** — the tail (`fastapi`, `uvicorn`, `slowapi`, `google-cloud-*`, `firebase-admin`, `tzdata`, `google-cloud-secret-manager`) is unpinned; the Dockerfile also `pip install`s fastapi/uvicorn again. The Dockerfile is named lowercase `dockerfile`, base **python:3.11-slim**, while the local venv is **Python 3.14.0** — the dependency check must be re-verified on 3.11 (Docker build does that).
6. **No CI** (`.github/` absent), **no pytest** in requirements, and `tests/test_fetch.py` is effectively empty (3 bytes). `selection_test.py` at root is a manual script.
7. `claude_call_with_retry` is duplicated in agent1b and agent2b; each agent has its own `_record_failure` (agent1b's is at `:435`). Both stay.
8. `data/` is gitignored, so a local run overwrites `data/news_filtered.json` — I'll back up any existing copy first.
9. The local dry-run of `pip install --dry-run -r requirements.txt langgraph langsmith` (py3.14) resolves **without changing any pin**: adds `langgraph 1.2.12, langgraph-checkpoint 4.2.0, langgraph-prebuilt 1.1.0, langgraph-sdk 0.4.5, langchain-core 1.6.6, langchain-protocol 0.0.19, langsmith 0.14.2, jsonpatch, jsonpointer, orjson, ormsgpack, requests-toolbelt, tenacity, uuid_utils, xxhash, zstandard, websockets, truststore, httpx2, httpcore2`. pydantic 2.13.4, typing_extensions 4.15.0, anyio 4.13.0, httpx 0.28.1 all stay. (Separate dry-run of langgraph alone wanted to bump these; irrelevant once pinned.) `langsmith` is not currently installed, so it's added explicitly. Note `httpx2`/`httpcore2` are new — will check what they are before pinning.

## Files
| File | Change |
|---|---|
| `agents/article_fetch.py` (new) | `fetch_article_text`, GitHub helpers, `_parse_lock`, `FETCH_TIMEOUT`, `USER_AGENT`, moved verbatim + a `fetch_article_text_result()` variant returning `(text, error_reason)` for the tool |
| `agents/agent2b_summarize_news.py` | delete moved code, `from article_fetch import fetch_article_text, ...`; no behavior change |
| `agents/agent1b_graph.py` (new) | `AgentState`, nodes, routing, `build_graph()`, `run_graph()` |
| `agents/agent1b_fetch_news.py` | keep all existing functions; `run()` dispatches on `AGENT1B_MODE` (`graph` default / `single_pass`); shared output-writing helper so both paths write the identical file |
| `agents/filter_tool.py` | add `FILTER_TOOL_WITH_CONFIDENCE` (new object; original `FILTER_TOOL` untouched for single-pass) + `FETCH_ARTICLE_TOOL`, `SUBMIT_CATEGORY_TOOL` |
| `prompts/news_filter_prompt.txt` unchanged; new `prompts/news_filter_confidence_addendum.txt`, `prompts/news_review_prompt.txt` | |
| `agents/tracing.py` (new) | opt-in LangSmith client wrapper + flush |
| `requirements.txt` | pin the full resolved set (re-resolved under 3.11 in Docker); `requirements-dev.txt` (new) with `pytest` |
| `tests/test_agent1b_graph.py`, `tests/conftest.py`, `tests/test_agent1b_single_pass.py` | new; `tests/test_fetch.py` left alone |
| `.github/workflows/tests.yml` | new, pytest only, no secrets, Python 3.11 |
| `README.md`, `CLAUDE.md`, `docs/decisions/0001-langgraph-inside-agents.md`, `docs/plans/…` | docs |

NOT touched: `main.py`, `orchestrator.py`, `_ISOLATED_RETRIES`, watchdog, `cloudbuild*.yaml`, `dockerfile` (unless the build proves it needs it — I'd ask), any Cloud Run config.

## State schema (`TypedDict`)
```python
class ArticleRecord(TypedDict, total=False):   # audit row, keyed by url
    url: str; first_pass_category: str; confidence: int
    routed_to_review: bool; review_status: str   # "skipped"|"reviewed"|"review_failed"|"capped"
    final_category: str; tool_calls: int; input_tokens: int; output_tokens: int

class AgentState(TypedDict):
    run_id: str
    raw_articles: list[dict]                       # fetched (HN + NewsAPI)
    articles: list[dict]                           # after prefilter / language filter
    categorized: list[dict]                        # article + category + confidence (internal only)
    confident: list[dict]; low_confidence: list[dict]
    reviewed: Annotated[list[dict], operator.add]  # reducer: parallel reviews merge
    audit: Annotated[list[ArticleRecord], operator.add]
    tool_call_counts: Annotated[dict, merge_counts]
    token_usage: Annotated[dict, merge_counts]     # per node: {node: {in, out}}
    review_budget: int                             # remaining cap
    final: list[dict]                              # exactly what the old code returned
```
Review subgraph state (per article): `article`, `messages`, `iterations`, `decision`, `error`.

## Graph
```
START → fetch → prefilter → language_filter → categorize ──route──┬─(all confident / cap=0)──────────────→ finalize → END
                                                                   └─(low confidence, ≤cap, via Send per article)→ review ─┘
review (subgraph):  llm_call ──(tool_use: fetch_article_text)──▶ tool_exec ──▶ llm_call
                        └──(submit_category | iteration cap | error)──▶ END
```
- `fetch`, `prefilter`, `language_filter` wrap the existing functions unchanged (fetch as a node so `run_graph` is one `invoke`; a fetch exception propagates — same as today).
- `route` is a conditional edge returning `Send("review", …)` per low-confidence article (highest-uncertainty first, truncated at `REVIEW_MAX_ARTICLES`); if none, goes straight to `finalize`. Parallelism bounded by the existing `_semaphore` (5) and `max_concurrency`.
- `review` = compiled subgraph with `llm_call`, `tool_exec`, and a conditional edge back while the last message contains a `fetch_article_text` tool_use. Max `REVIEW_MAX_ITERATIONS` (default 3) LLM calls per article.
- `finalize` merges reviewed categories over first-pass ones, strips `confidence`, writes results as `{**article, "category": …}` — identical shape to today — and returns `final`. `run()` then writes the same file/Firestore payload as today. Diagram in README from `build_graph().get_graph(xray=True).draw_mermaid()`.

## Key design decisions
- **Confidence scale:** integer 1–5 with anchored definitions in the prompt (5 = unambiguous, 3 = plausible alternative category, 1 = guess). Chosen over a 0–1 float because LLM self-reported floats are false-precise and cluster at 0.9+; anchored ordinal bands are easier to threshold and to calibrate later. `REVIEW_CONFIDENCE_THRESHOLD` default 4 (review if confidence < 4) — I'll look at the real distribution in the local run and tune before finalizing defaults.
- **Env vars (all with safe defaults):** `AGENT1B_MODE=graph|single_pass`, `REVIEW_CONFIDENCE_THRESHOLD=4`, `REVIEW_MAX_ARTICLES=30`, `REVIEW_MAX_ITERATIONS=3`, `REVIEW_FETCH_TIMEOUT=10`. Setting `REVIEW_MAX_ARTICLES=0` disables review without a code path change; `AGENT1B_MODE=single_pass` is the no-redeploy rollback for Cloud Run.
- **Terminal tool:** the review model gets two tools: `fetch_article_text(url)` and `submit_category(category, reason)`. `submit_category` is handled inside `llm_call` (it's the structured "final answer"; only `fetch_article_text` runs in `tool_exec`). Slight stretch of "one tool"; alternative is free-text parsing, which I'd avoid.
- **Failure policy:** fetch error/timeout/thin content → tool_result says so; model may still submit from title+description. If the loop errors, hits the cap without a decision, or the LLM call fails after retries → keep first-pass category, `review_status="review_failed"`. Only unexpected exceptions outside review reach `_record_failure` + re-raise (existing wrapper in `run()` unchanged).
- **Retry/concurrency/model:** reuse `claude_call_with_retry` (10/20/40s backoff), `_semaphore`, `SCORING_MODEL` from `config.py`. Tokens are accumulated from `response.usage` via an optional meter argument (default `None` → single-pass code behaves exactly as before).
- **Audit ("built for eval"):** per-article `ArticleRecord` rows written to `data/agent1b_review_log.json` (new file; existing outputs unchanged). Cloud containers have ephemeral disk, so in cloud mode I'd also write it to a separate doc `agent1b_audits/{run_id}` (NOT the run doc — 1 MiB history), plus a tiny additive summary (`agent1b_review_summary`: counts + tokens) on the run doc. See open question 2.
- **Tracing:** `agents/tracing.py`: if `LANGSMITH_TRACING=true` **and** `LANGSMITH_API_KEY` set → `langsmith.wrappers.wrap_anthropic(client)` (per-call tokens/cost), and LangGraph's own auto-tracing gives node spans; otherwise returns the plain client (no-op, no langsmith import cost at call time). `@traceable` on `fetch_article_text` tool. I'll verify `wrap_anthropic` works with `anthropic 0.109.1` / `langsmith 0.14.2` in a test with tracing off and a local dry check with a fake endpoint. Secrets: API keys live only in client constructors/env, not in message inputs; NewsAPI key is only in `requests` params (unwrapped). Call `Client().flush()` at end of `run()` (Cloud Run throttles CPU after the response, and the watchdog can `os._exit`). Documented (not applied): `gcloud run deploy agent1b --set-secrets LANGSMITH_API_KEY=langsmith-api-key:latest --set-env-vars LANGSMITH_TRACING=true`, after `gcloud secrets create` + `secretmanager.secretAccessor` for the runtime SA.

## Increments (branch `feat/langgraph-agent1b`; no commits unless you ask — per CLAUDE.md, commits will be incremental when you do)
1. Branch, save plan to `docs/plans/`, add `requirements-dev.txt`, pytest scaffold + pins; verify `pytest` runs, `import main` for all AGENT_NAME values.
2. Extract `article_fetch.py`; agent2b imports it. Verify: import agent2b, unit test of fetch helper with stubbed `Article`, diff shows moved code identical.
3. Tool schemas + prompts; `agent1b_graph.py` state + nodes + graph; `run()` dispatch with `single_pass` default-off wiring. Tests: routing, loop cap, fetch failure, output keys, single-pass path.
4. Audit log + tracing module + tests (tracing no-op with env unset).
5. Dependency pins under 3.11 + Docker build (baseline image size first, then after; report delta; run `import main` per AGENT_NAME inside the image or via env loop).
6. CI workflow, README (Mermaid generated from the compiled graph), ADR 0001, CLAUDE.md.
7. Local end-to-end run of `agents\agent1b_fetch_news.py` in both modes; compare output schemas.

## Test plan (pytest, no network/keys)
Fake Anthropic client (records calls, returns scripted `tool_use` responses with `usage`) and monkeypatched `fetch_*` / fetcher: (a) confidence < threshold → review, ≥ → finalize; (b) review cap `REVIEW_MAX_ARTICLES` respected, overflow marked `capped`; (c) model that always requests `fetch_article_text` stops at `REVIEW_MAX_ITERATIONS` and degrades to first-pass; (d) fetch raises/times out → `review_failed`, run doesn't fail; (e) output JSON has exactly the original top-level keys and per-article keys (no `confidence` leak); (f) `AGENT1B_MODE=single_pass` calls the untouched original functions and produces the same shape; (g) `_record_failure` still called and exception re-raised on unexpected error; (h) tracing helper returns unwrapped client when env unset. Dev commands (CMD): `venv\Scripts\python -m pip install -r requirements-dev.txt` then `venv\Scripts\python -m pytest -q`.

## Docker / cloud-safety checks
Baseline `docker build` (size via `docker images`), rebuild after, report delta. Because Docker's `python:3.11-slim` differs from my 3.14 venv, pins are generated/validated in 3.11. For each of `agent1a agent1b agent2a agent2b agent3 agent4 orchestrator healthcheck agent_subscriptions`: `set AGENT_NAME=<x>` then `python -c "import main"` (CMD loop). Memory/CPU: langgraph adds import weight to agent1b only (lazy import in `run()`), so other services' cold start is unaffected — but the shared image grows for all 9 services.

## Cost estimate for the local run (before I run it — I will ask you first)
Rough, Haiku 4.5 (~$1/M in, ~$5/M out): existing pipeline ≈ language filter (~1,500 articles × ~150 tok in) + categorize (~1,000 × ~80 tok in, ~30k out) ≈ **$0.4–0.6**. Review adds ≤30 articles × ≤3 calls × ~3k tok ≈ **$0.1–0.3**. Running both modes (to compare schemas) ≈ **$1.0–1.5 total** plus NewsAPI quota (10 requests/run). Precise numbers will come from the token meter afterward.

## Open questions
1. **Default mode in prod:** ship `AGENT1B_MODE` defaulting to `graph` (rollback = env var) or default `single_pass` until you've watched a run? I recommend `graph` default since you want it running in production.
2. **Where the cloud audit log goes:** separate Firestore collection `agent1b_audits/{run_id}` (recommended) vs. stdout-only structured log vs. a GCS file. Adds a new collection; no rules change needed (Admin SDK).
3. **Terminal `submit_category` tool** in addition to `fetch_article_text` — OK?
4. **Dockerfile / pins:** OK to leave the unpinned tail of `requirements.txt` alone (only pin the new langgraph set), or do you want a full re-freeze on 3.11?
5. **Confidence threshold default (4) and cap (30):** fine as starting points, tuned after the local run?
6. `[internal]` at repo root is a tracked odd file; untouched unless you say otherwise.
