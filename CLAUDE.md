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

- **agent1a** – Fetches cs.AI/cs.LG papers from ArXiv (up to 500, last 7 days), randomly samples 35, scores with Claude using a 7-dimension 28-point rubric (`scoring_rubric.txt`), keeps top 3 (`PAPERS_IN_NEWSLETTER`). Max 5 concurrent Claude calls with exponential backoff (10s/20s/40s). PDF/API fetches route through a Squid proxy (`HTTPS_PROXY` / `HTTP_PROXY` env vars) because GCP IPs are throttled by ArXiv — both the `urllib` opener and the `arxiv` library session are patched.
- **agent1b** – Fetches Hacker News + 10 NewsAPI queries (English global, French global, Canada/Montreal), filters paywalled/non-Latin in code, then Claude language-filters (EN/FR only) and categorizes into 7 categories (`filter_tool.py` schema). Max 5 concurrent Claude calls.
- **agent2a/2b** – Summarize papers/articles in parallel threads; use Firestore atomic counter (`agent2_completions`) to sync before triggering agent3. The agent that increments the counter to 2 publishes `content-summarized`.
- **agent3** – Runs two article-selection passes per category (all-language + English-only) to support subscriber preference variants. Claude selects the best 3-5 articles per category; HN ≥ 100 articles are always included. Writes the editor's intro, then renders **4 HTML variants** keyed by `{include_french}_{include_canada}` (`0_0`, `1_0`, `0_1`, `1_1`). Saves all variants to Firestore and copies `0_0` to `public/newsletter/latest.html` for the live preview.
- **agent4** – Triggered by Cloud Scheduler at 7 AM Monday (pipeline runs at 6 AM). In cloud mode, loads all 4 newsletter variants from Firestore, queries active subscribers, picks each subscriber's variant by preference key, substitutes `{{UNSUBSCRIBE_URL}}` and `{{PREFERENCES_URL}}` per subscriber, and sends via SendGrid. In local mode, sends a single copy to `TEST_RECIPIENT_EMAIL`.

### Subscription Service

`agents/agent_subscriptions.py` is a standalone FastAPI app (no `run(run_id)` function). Deployed as a separate Cloud Run service. Firestore collection: `subscribers`.

**Two auth paths coexist:**

**Token-based (legacy, email links):** "Inbox is the auth" — tokens only travel inside emails. Still used for newsletter footer links (unsubscribe, preferences).
- `POST /subscribe` — double opt-in; sends confirmation email.
- `POST /request-unsubscribe` — sends unsubscribe confirmation email (always 200).
- `POST /request-preferences` — sends preferences magic link email (always 200).
- `GET /confirm?token=` — activates subscription; rotates token to 365d action token.
- `GET /unsubscribe?token=` — deactivates subscription.
- `GET/POST /preferences?token=` — read or update preferences.

**Account-based (Firebase Auth, `Authorization: Bearer <id_token>`):** Users sign in via Google or email+password through `login.html`. Firebase ID token verified in `agents/auth_middleware.py` using `firebase-admin`. No confirmation email needed — Firebase already verified the email. Creates a `users/{uid}` doc on first call.
- `GET /auth/me` — return user info + subscription status.
- `POST /auth/subscribe` — subscribe instantly (email already verified; requires `email_verified: true`).
- `POST /auth/unsubscribe` — deactivate subscription.
- `GET /auth/preferences` — return prefs.
- `POST /auth/preferences` — update prefs.

**Subscriber doc fields:** `email`, `token`, `token_expires_at`, `active`, `subscribed_at`, `confirmed_at`, `prefs: {include_french, include_canada}`, `send_latest`, `latest_sent`, `uid` (Firebase UID, null for legacy subscribers). Token TTL: 48h for confirmation, 365d for action links.

**Firestore collections:** `subscribers` (existing), `users` (new — doc ID = Firebase UID, fields: `email`, `display_name`, `provider`, `created_at`).

`main.py` conditionally mounts the subscription router when `AGENT_NAME=agent_subscriptions`. CORS `allow_headers` includes `Authorization` for the account-based routes.

### Firebase Auth Setup (one-time, manual)

In Firebase Console → Authentication → Sign-in method:
1. Enable **Google** provider
2. Enable **Email/Password** (standard, not email link)
3. Add authorized domains: `newsletter.lofeodo.com`, `latentspacemail.web.app`

`auth.js` loads the Firebase project config automatically from `/__/firebase/init.json`, which Firebase Hosting serves on all deployments. **No API key in source.** For local frontend development with auth, run `firebase serve --only hosting` instead of a plain HTTP server (plain servers don't serve that endpoint).

For local development of the subscription service, Firebase Admin SDK uses Application Default Credentials: `gcloud auth application-default login`. On Cloud Run, ADC works automatically.

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
| `TEST_RECIPIENT_EMAIL` | Local mode only: single send address for agent4 |
| `SERVICE_BASE_URL` | Public URL of the subscription service (for confirmation/unsubscribe links) |
| `FRONTEND_BASE_URL` | Public URL of the Firebase Hosting frontend (for preferences magic links) |
| `HTTPS_PROXY` / `HTTP_PROXY` | Squid proxy URL for agent1a ArXiv fetches (GCP IPs are throttled) |
| `ALLOWED_ORIGINS` | CORS origins for subscription API (comma-separated; required in production) |
| `TEST_SEND_TO` | Cloud mode: skip subscriber list and send only to this address (test runs) |
| `MAILING_ADDRESS` | Physical address in email footer (CASL compliance) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Local only: path to service account JSON for Firebase Admin SDK (alternative to `gcloud auth application-default login`) |

## Pub/Sub Topics (Cloud Mode)

`pipeline-start` → `papers-scored` + `news-filtered` → `content-summarized` → (agent3 runs) → agent4 triggered separately by Cloud Scheduler.

## Prompts

All Claude prompts are in `prompts/`. Edit prompt files to change scoring behavior, summary style, or category definitions without touching Python code.
