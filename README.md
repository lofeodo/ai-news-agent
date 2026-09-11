# Latent SpaceMail

A weekly agentic pipeline that automatically curates and delivers a morning AI briefing, combining selected AI-research papers from ArXiv with the top AI-industry news from live sources, as a personalized HTML email newsletter. Built on Google Cloud Platform with six specialized agents orchestrated via Pub/Sub and Firestore.

---

## Pipeline

```mermaid
flowchart TB
    subgraph SUB["Subscription subsystem"]
        direction LR
        FE["🌐 Firebase Hosting\nlofeodo.com/newsletter/"] <--> SUBS["Subscription API\nCloud Run · FastAPI"]
        SUBS <--> FSS[("Firestore\nsubscribers")]
    end

    SUB ~~~ CS1

    CS1["☁️ Cloud Scheduler — 6 AM Monday"] --> ORC["Orchestrator · Cloud Run"]

    ORC -->|"Pub/Sub: pipeline-start"| A1A["Agent 1a\nFetch & score ArXiv papers\nup to 500 → 35 sampled → top 3"]
    ORC -->|"Pub/Sub: pipeline-start"| A1B["Agent 1b\nFetch HN + NewsAPI\nlanguage-filter → categorize"]

    A1A -->|"Pub/Sub: papers-scored"| A2A["Agent 2a\nDownload PDFs\nwrite paper mini-reviews"]
    A1B -->|"Pub/Sub: news-filtered"| A2B["Agent 2b\nFetch article text\nwrite news summaries"]

    A2A -->|"Firestore atomic counter\nagent2_completions"| FAN{"Fan-in\ncount == 2?"}
    A2B --> FAN

    FAN -->|"Pub/Sub: content-summarized"| A3["Agent 3\nSelect articles · write intro\ncompose 4 HTML variants\nsave to Firestore"]

    FAN ~~~ CS2
    CS2["☁️ Cloud Scheduler — 7 AM Monday"] --> A4["Agent 4\nLoad latest newsletter\npersonalize per subscriber\nsend via SendGrid"]

    A3 --> FSP[("Firestore\npipeline_runs")]
    A4 --> FSP

    FAN ~~~ CS3
    CS3["☁️ Cloud Scheduler — 7:10 AM Monday"] --> HC["Health Check\nFind latest run · diagnose\nemail alert if unhealthy"]
    FSP --> HC
```

---

## How it works

### Stage by stage

**Agent 1a — Fetch & score papers**
Queries ArXiv for `cs.AI + cs.LG` papers from the last 7 days (up to 500), randomly samples 35, downloads each PDF, and scores it via Claude forced tool use against a 7-dimension, 28-point rubric (`prompts/scoring_rubric.txt`). Top 3 by score advance. PDF fetches route through a Squid proxy (set via `HTTPS_PROXY`) because GCP IPs are throttled by ArXiv. Up to 5 concurrent Claude calls, exponential backoff on 429s (10s / 20s / 40s).

**Agent 1b — Fetch & filter news**
Pulls top stories from the Hacker News API and runs 10 NewsAPI queries (English global, French global, Canada/Montreal). Pre-filters paywalled domains and non-Latin titles in code, then uses Claude to language-filter (English/French only) and categorize into 7 categories. Up to 5 concurrent Claude calls in 200-article batches.

**Agent 2a — Summarize papers**
Downloads PDFs again (falls back to abstract + scoring notes if unavailable), calls Claude to write a 2-paragraph mini-review per paper. Up to 5 concurrent calls.

**Agent 2b — Summarize news**
Fetches full article text with `newspaper3k` (GitHub repos via the GitHub API, Twitter/X URLs skipped). Calls Claude for a 2-3 sentence summary per article. Up to 3 concurrent Claude calls, 20 concurrent fetch workers.

**Fan-in**
Both agent 2a and 2b atomically increment `agent2_completions` in the Firestore run document using a Firestore transaction. The one that pushes the counter to 2 publishes `content-summarized` to trigger agent 3.

**Agent 3 — Compose**
Runs two article-selection passes per category (all languages, English-only) to support subscriber preference variants. Calls Claude to pick the best 3-5 articles per category (HN ≥ 100 = always included, named model releases always included). Writes a 2-3 sentence editor's note. Renders 4 HTML variants keyed by `{include_french}_{include_canada}`. Saves all variants to Firestore and copies `0_0` to `public/newsletter/latest.html` for the live preview.

**Agent 4 — Send**
Triggered separately by Cloud Scheduler at 7 AM. Loads the most recent run's newsletter variants from Firestore, queries active subscribers, picks each subscriber's variant by preference key, substitutes `{{UNSUBSCRIBE_URL}}` and `{{PREFERENCES_URL}}` placeholders with per-subscriber token links, and sends via SendGrid. Logs a structured JSON send summary to stdout for Cloud Logging, and also writes it to the run's Firestore document (`agent4_send_summary`, `agent4_completed_at`) so delivery success is queryable, not just visible in logs. Refuses to send (raises instead) if the newsletter it found is more than 24 hours old — a stalled pipeline upstream of agent 3 otherwise leaves the most recent *composed* run pointing at an older week's content, which agent 4 would ship silently with no error. See `CLAUDE.md` for the incident that motivated this.

**Health check**
A standalone agent, `agent_healthcheck.py`, triggered separately by Cloud Scheduler at 7:10 AM — shortly after agent 4's send — rather than by Pub/Sub, so it has no `run_id` handed to it; it looks up the most recent `pipeline_runs` document itself. It flags a stale run (started more than 4 hours ago with no completion), any recorded agent failure, or a missing pipeline stage, and emails a report every run — a weekly heartbeat that says "all clear" or lists what's wrong, rather than only emailing on failure. Never touches the subscribers collection. See `CLAUDE.md` for the full failure-recording and detection mechanics.

### Subscription system

A separate FastAPI service handles sign-ups and preferences. Two auth paths coexist:

**Token-based (email links):** The original "inbox is the authentication" model. Website-initiated actions trigger an email round-trip; token-carrying links clicked inside an email prove inbox ownership. Tokens are `secrets.token_urlsafe(32)`, 48h TTL for confirmation, 1-year TTL for action links. Still used for newsletter footer links (unsubscribe, preferences) for all subscribers.

**Account-based (Firebase Auth):** Users sign up or sign in via `login.html` using Google OAuth or email + password. The frontend gets a Firebase ID token and sends it as `Authorization: Bearer <token>`. The backend (`agents/auth_middleware.py`) verifies it with `firebase-admin`. No confirmation email needed — Firebase handles email verification. Account-based subscribers can manage preferences and unsubscribe directly without waiting for an email link.

Subscriber document fields: `email`, `token`, `token_expires_at`, `active`, `subscribed_at`, `confirmed_at`, `prefs: {include_french, include_canada}`, `send_latest`, `latest_sent`, `uid` (Firebase UID, null for legacy token-only subscribers). Unsubscribe sets `active: false` (soft delete, never hard-deleted).

---

## Tech stack

- **Language:** Python 3.11
- **Compute:** Google Cloud Run (single Docker image, `AGENT_NAME` env var selects agent)
- **Messaging:** Google Cloud Pub/Sub (push subscriptions, JSON `{run_id}` payload)
- **State:** Google Cloud Firestore (`pipeline_runs`, `subscribers`, `users` collections)
- **Scheduling:** Google Cloud Scheduler (weekly cron jobs: pipeline start, newsletter send, post-send health check)
- **Secrets:** Google Secret Manager
- **Auth:** Firebase Authentication (Google OAuth + email/password; ID tokens verified server-side with `firebase-admin`)
- **Frontend:** Firebase Hosting (static, custom domain via Cloudflare DNS; vanilla HTML/JS + Firebase Auth JS SDK)
- **Email:** SendGrid (custom domain `newsletter@lofeodo.com`, DKIM + SPF + DMARC)
- **AI:** Anthropic Claude (`claude-haiku-4-5-20251001`) — scoring, filtering, summarization, composition
- **External APIs:** ArXiv (via DigitalOcean Squid proxy), Hacker News API, NewsAPI, GitHub API
- **HTTP framework:** FastAPI + uvicorn
- **Key libraries:** `arxiv`, `pypdf`, `newspaper3k`, `slowapi`, `firebase-admin`

---

## Repository structure

```
.
├── agents/
│   ├── agent1a_fetch_papers.py     # ArXiv fetch + Claude scoring
│   ├── agent1b_fetch_news.py       # HN + NewsAPI fetch, language filter, categorize
│   ├── agent2a_summarize_papers.py # PDF download + Claude paper reviews
│   ├── agent2b_summarize_news.py   # Article fetch + Claude news summaries
│   ├── agent3_compose.py           # Article selection, intro, HTML composition
│   ├── agent4_send.py              # Per-subscriber personalization + SendGrid send
│   ├── agent_healthcheck.py        # Standalone weekly pipeline health check + alert
│   ├── agent_subscriptions.py      # Subscription FastAPI service (separate deployment)
│   ├── auth_middleware.py          # Firebase ID token verification (FastAPI dependency)
│   ├── filter_tool.py              # Claude tool schema for news categorization
│   └── scoring_tool.py             # Claude tool schema for paper scoring
├── prompts/
│   ├── scoring_rubric.txt          # 7-dimension paper scoring prompt
│   ├── paper_summary_prompt.txt    # Paper mini-review prompt
│   ├── news_filter_prompt.txt      # News categorization prompt
│   ├── news_summary_prompt.txt     # News article summary prompt
│   ├── news_summary_fallback_prompt.txt
│   ├── article_selection_prompt.txt
│   ├── intro_prompt.txt            # Editor's note prompt
│   └── quebec_french_style.txt     # French-language style guide for news summaries
├── public/newsletter/              # Firebase Hosting frontend
│   ├── index.html                  # Subscribe form (auth-aware nav)
│   ├── login.html                  # Sign in / create account (Google + email+password)
│   ├── preferences.html            # Preferences (account auth or token fallback)
│   ├── unsubscribe.html            # Unsubscribe (one-click if signed in, email form otherwise)
│   ├── preview.html                # Newsletter preview page
│   ├── sections.html               # Premium newsletter-sections customization UI
│   ├── auth-callback.html          # Google Sign-In exchange-code redemption landing page
│   ├── auth.js                     # Shared Firebase Auth helper (ES module)
│   ├── nav.js                      # Shared auth-aware navigation bar
│   ├── bg.js                       # Shared background/decorative script
│   ├── style.css / fonts.css       # Shared styling
│   ├── fonts/, images/             # Static assets
│   └── latest.html                 # Written by agent3 each run
├── orchestrator.py                 # Local sequential runner / cloud pipeline trigger
├── main.py                         # Cloud Run entrypoint (FastAPI, AGENT_NAME dispatch)
├── config.py                       # Shared constants and env var reads
├── Dockerfile                      # Single image, AGENT_NAME build arg
├── cloudbuild.yaml                 # Cloud Build: build + push all 9 service images
├── cloudbuild-partial.yaml         # Cloud Build: agent1b + agent3 + agent4 only (fast iteration)
├── cloudbuild-subscriptions.yaml   # Cloud Build: agent_subscriptions only
├── firebase.json                   # Firebase Hosting config
├── firestore.indexes.json          # Firestore composite index definitions
└── requirements.txt
```

---

## Configuration

Secrets live in **Google Secret Manager** (cloud) or environment variables (local). No secrets are committed to this repo.

| Variable | Used by | Purpose |
|---|---|---|
| `ANTHROPIC_1ST_API_KEY` | agent1a, agent2a, agent2b, agent3 | Claude API key |
| `NEWS_API_KEY` | agent1b | NewsAPI key |
| `SENDGRID_API_KEY` | agent4, agent_subscriptions | SendGrid key (local mode; cloud uses Secret Manager) |
| `USE_SECRET_MANAGER` | agent4, agent_subscriptions | Load SendGrid key from Secret Manager instead of env |
| `USE_FIRESTORE` | all agents | Enable cloud mode (Pub/Sub + Firestore); default `false` |
| `GCP_PROJECT_ID` | all agents | Google Cloud project ID |
| `HTTPS_PROXY` / `HTTP_PROXY` | agent1a | Squid proxy URL for ArXiv (GCP IPs are throttled) |
| `AGENT_NAME` | main.py | Selects which agent the Cloud Run container runs |
| `TEST_RECIPIENT_EMAIL` | agent4 | Local mode: single send address |
| `TEST_SEND_TO` | agent4 | Cloud mode override: skip subscriber list, send only here |
| `SERVICE_BASE_URL` | agent4, agent_subscriptions | Public URL of the subscription API service |
| `FRONTEND_BASE_URL` | agent3, agent4, agent_subscriptions | Public URL of the Firebase Hosting frontend — must be `https://newsletter.lofeodo.com` on every service that sets it; a mismatch here silently breaks the `{{PREFERENCES_URL}}` link in every sent newsletter |
| `ALLOWED_ORIGINS` | main.py (subscriptions) | Comma-separated CORS origins; required in production |
| `MAILING_ADDRESS` | agent3 | Physical address in email footer (CASL compliance) |
| `ADMIN_TOKEN` | agent_subscriptions | Token to access `/stats` endpoint |
| `MAX_SUBSCRIBERS` | agent_subscriptions | Subscriber cap (default `50000`) |
| `ALERT_EMAIL` | agent_healthcheck | Where the weekly pipeline health check sends a problem report; never used for subscriber-facing sends |
| `GOOGLE_APPLICATION_CREDENTIALS` | agent_subscriptions (local) | Path to service account JSON for Firebase Admin SDK; alternative to `gcloud auth application-default login` |
| `GOOGLE_OAUTH_CLIENT_ID` | agent_subscriptions | Google OAuth 2.0 Web client ID for server-side Google Sign-In (not secret) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | agent_subscriptions | Google OAuth 2.0 client secret (local mode; cloud uses Secret Manager, secret name `google-oauth-client-secret`) |

---

## Local development

**Run the full pipeline (no cloud infra required):**
```bash
export ANTHROPIC_1ST_API_KEY=sk-...
export NEWS_API_KEY=...
python orchestrator.py
# Outputs to data/ directory
```

**Run a single agent:**
```bash
python agents/agent1a_fetch_papers.py
python agents/agent2a_summarize_papers.py
# etc.
```

**Run the FastAPI server (Cloud Run entrypoint):**
```bash
AGENT_NAME=agent1a uvicorn main:app --reload
```

**Run the subscription service locally:**
```bash
AGENT_NAME=agent_subscriptions uvicorn main:app --reload
# Requires: gcloud auth application-default login (Firestore always on)
```

**Run the frontend locally with auth support:**
```bash
firebase serve --only hosting
# Serves public/newsletter/ at localhost:5000. auth.js hardcodes its Firebase
# config now (no longer fetches /__/firebase/init.json), but Firebase
# Hosting's /__/auth/action pages (password reset / email verification
# continue links) still require this emulator -- a plain HTTP server won't
# serve those paths.
```

## Deployment

**Build and push to Artifact Registry:**
```bash
docker build --build-arg AGENT_NAME=agent1a -t REGION-docker.pkg.dev/PROJECT/REPO/agent1a .
docker push REGION-docker.pkg.dev/PROJECT/REPO/agent1a
```

**Or build all services at once via Cloud Build:**
```bash
gcloud builds submit --config cloudbuild.yaml
```
Builds and pushes all 9 service images in parallel. `cloudbuild-partial.yaml` builds only agent1b/agent3/agent4 (a faster subset for iterating on the news→compose→send path); `cloudbuild-subscriptions.yaml` builds only agent_subscriptions. None of these three deploy to Cloud Run — that step is always the separate, manual `gcloud run deploy` below, on purpose: each service needs different env vars, and rolling out a new Cloud Run revision is a live-traffic change that's deliberately not automatic on every build.

**Deploy to Cloud Run:**
```bash
gcloud run deploy agent1a \
  --image REGION-docker.pkg.dev/PROJECT/REPO/agent1a \
  --region REGION \
  --no-cpu-throttling \     # required for pipeline agents (background thread)
  --set-env-vars AGENT_NAME=agent1a,USE_FIRESTORE=true,...
```

The subscription service and agent4 (sender) are synchronous and don't need `--no-cpu-throttling`.

---

## Design decisions

**Event-driven fan-in.** Agents 2a and 2b run in parallel (both triggered by their respective Pub/Sub messages). A Firestore atomic transaction increments `agent2_completions`; the agent that pushes the count to 2 publishes `content-summarized`. This avoids a coordinator process and handles the race condition correctly under concurrent Cloud Run instances.

**ArXiv proxy.** GCP datacenter IPs are rate-limited or blocked by ArXiv's CDN. A DigitalOcean-hosted Squid proxy is set via `HTTPS_PROXY`; both the `urllib` opener and the `arxiv` library's internal `requests.Session` are patched to use it.

**No CPU throttling on pipeline agents.** Cloud Run's default "CPU only allocated during request" would pause the background thread immediately after the HTTP response is returned. Pipeline agents use `--no-cpu-throttling` so the thread runs to completion. Synchronous services (agent4, subscriptions) don't need this.

**Hard runtime watchdog.** `main.py` arms a `threading.Timer` around every agent: whichever is sooner of 1 hour after start, or 07:30 America/Toronto for a run that started before it (a manual daytime recovery run only gets the 1-hour cap). On expiry it records `{agent}_error`/`{agent}_failed_at` to the run's Firestore doc, then `os._exit(124)` — a hard process exit that works even if the agent thread is wedged in a C extension, which a Python-level timeout wouldn't survive. Added after agent2b died from a native SIGABRT with no trace left behind; the watchdog bounds hangs going forward, though it can't help a process that has already crashed on its own.

**Dual auth model.** The subscription service supports two auth paths. The original "inbox as auth" token model (email links) remains fully functional for newsletter footer links and legacy subscribers. A new account-based path uses Firebase Authentication (Google OAuth + email/password): the frontend gets a Firebase ID token and sends it as `Authorization: Bearer`; `auth_middleware.py` verifies it with `firebase-admin`. Account-based subscribers get immediate subscribe/unsubscribe/preferences without waiting for an email — the Firebase auth flow already verified inbox ownership. Both paths read and write the same `subscribers` Firestore collection; account subscribers get a `uid` field linking them to the `users` collection.

**Email deliverability.** Mail sends from `newsletter@lofeodo.com` via SendGrid with full domain authentication (DKIM + SPF via CNAME records, DMARC policy). Sending from a gmail.com address through a third-party relay fails SPF alignment and lands in spam — a controlled sending domain is required.

**Soft delete.** Unsubscribing sets `active: false`; the document is never deleted. This preserves the audit trail and allows re-subscription without losing history.

**Subscriber variants.** Agent 3 generates four newsletter HTML variants keyed by `{include_french}_{include_canada}` (`0_0`, `1_0`, `0_1`, `1_1`). Agent 4 picks the correct variant per subscriber at send time, so no re-rendering is needed per send.

**Failure recording over silent stalls.** Every pipeline agent's top-level exception is caught and recorded to its `pipeline_runs` document (`{agent}_error`, `{agent}_failed_at`) before re-raising, rather than only surfacing in Cloud Logging. Without this, one agent failing partway through leaves the run permanently incomplete with no durable trace of why — the standalone health check agent depends on these fields being present to report a specific cause rather than just "something didn't finish." See `CLAUDE.md` for the full mechanics, including a known limitation: the health check's own alert email shares SendGrid with the real newsletter send, so a SendGrid-specific outage can suppress the alert about the very failure it's meant to catch.
