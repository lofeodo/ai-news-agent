# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Weekly AI newsletter pipeline. Six agents fetch ArXiv papers + news, score/summarize them with Claude, compose an HTML email, and send it via SendGrid. A separate subscription service manages subscriber preferences.

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

**Run the FastAPI server locally (cloud mode entrypoint):**
```bash
AGENT_NAME=agent1a uvicorn main:app --reload
```

**Docker build per agent:**
```bash
docker build --build-arg AGENT_NAME=agent1a -t ai-news-agent-1a .
```

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
```

- **agent1a** – Fetches cs.AI/cs.LG papers from ArXiv, scores with Claude using a 7-dimension rubric (`scoring_rubric.txt`), keeps top 5. Max 5 concurrent Claude calls with exponential backoff.
- **agent1b** – Fetches Hacker News + 9 NewsAPI queries, filters paywalled/non-Latin, then has Claude categorize into 7 categories (`filter_tool.py` schema).
- **agent2a/2b** – Summarize papers/articles in parallel threads; use Firestore atomic counter to sync before triggering agent3.
- **agent3** – Claude selects articles per category and writes an intro; renders final `newsletter.html`.
- **agent4** – Triggered by Cloud Scheduler at 7 AM Monday; reads most recent newsletter from Firestore and sends via SendGrid.

### Subscription Service

`agents/agent_subscriptions.py` is a standalone FastAPI app (no `run(run_id)` function). Deployed as a separate Cloud Run service. Email round-trip is the authentication model — no passwords. Routes: `/subscribe`, `/confirm`, `/unsubscribe`, `/preferences`. Firestore collection: `subscribers`.

`main.py` conditionally mounts the subscription router when `AGENT_NAME=agent_subscriptions`.

### Claude Tool Use Pattern

Structured outputs use tool use instead of parsing free text:
- `scoring_tool.py` – `score_paper` tool with 7-dimension schema
- `filter_tool.py` – `filter_articles` and `filter_by_language` tools

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
| `NEWSLETTER_RECIPIENT_EMAIL` | Send address for agent4 |
| `SERVICE_BASE_URL` | Subscription service URL (for confirmation links) |
| `ALLOWED_ORIGINS` | CORS origins for subscription API |

## Pub/Sub Topics (Cloud Mode)

`pipeline-start` → `papers-scored` + `news-filtered` → `content-summarized` → (agent3 runs) → agent4 triggered separately by Cloud Scheduler.

## Prompts

All Claude prompts are in `prompts/`. Edit prompt files to change scoring behavior, summary style, or category definitions without touching Python code.
