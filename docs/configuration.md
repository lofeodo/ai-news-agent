# Configuration

Credentials (API keys and similar) are supplied to the services at runtime and are never committed to this repo. The table below lists the non-secret configuration.

| Variable | Used by | Purpose |
|---|---|---|
| `USE_FIRESTORE` | all agents | Enable cloud mode (Pub/Sub + Firestore); default `false` |
| `GCP_PROJECT_ID` | all agents | Google Cloud project ID |
| `AGENT1B_MODE` | agent1b | `graph` (default, LangGraph) or `single_pass` (original linear code; rollback switch) |
| `AGENT2B_MODE` | agent2b | `all` (default, summarize every article) or `shortlist` (summarize only what a first selection pass keeps; cuts ~65% of agent2b's cost, see the Step 3 notes in `cost-reduction-roadmap.md`). Read on every run, so changing it needs no redeploy |
| `REVIEW_CONFIDENCE_THRESHOLD` / `REVIEW_MAX_ARTICLES` / `REVIEW_MAX_ITERATIONS` / `REVIEW_FETCH_TIMEOUT` | agent1b | Review-loop tuning (defaults `4` / `0` / `3` / `10`; review is off by default, `REVIEW_MAX_ARTICLES=30` turns it on); see [Inside agent 1b](architecture.md#inside-agent-1b-langgraph) |
| `LANGSMITH_TRACING` / `LANGSMITH_PROJECT` | all Claude-calling agents | Opt-in LangSmith tracing; a no-op without an API key |
| `CLICK_TRACKING` | agent4 | `true` turns on SendGrid click tracking and tags each email with the run id (default off) |
| `TRENDING_LOOKBACK_DAYS` | agent1a | Days of Hugging Face Daily Papers considered for the shortlist (default `14`; a constant in `config.py`) |
| `JUDGE_MAX_ITEMS` / `JUDGE_MAX_USD` | healthcheck | Summaries judged per weekly run and its hard cost cap (constants in `config.py`) |
| `AGENT_NAME` | main.py | Selects which agent the Cloud Run container runs |
| `TEST_RECIPIENT_EMAIL` | agent4 | Local mode: single send address |
| `TEST_SEND_TO` | agent4 | Cloud mode override: skip subscriber list, send only here |
| `SERVICE_BASE_URL` | agent4, agent_subscriptions | Public URL of the subscription API service |
| `FRONTEND_BASE_URL` | agent3, agent4, agent_subscriptions | Public URL of the Firebase Hosting frontend — must be `https://newsletter.lofeodo.com` on every service that sets it; a mismatch here silently breaks the `{{PREFERENCES_URL}}` link in every sent newsletter |
| `ALLOWED_ORIGINS` | main.py (subscriptions) | Comma-separated CORS origins; required in production |
| `MAILING_ADDRESS` | agent3 | Physical address in email footer (CASL compliance) |
| `MAX_SUBSCRIBERS` | agent_subscriptions | Subscriber cap (default `40000`) |
| `RESPONSE_FLOOR_SECONDS` / `SENDGRID_TIMEOUT_SECONDS` | agent_subscriptions | Minimum response time of the mail routes, and the timeout of outgoing mail calls (defaults `2.0` / `8`) |
| `MAIL_COOLDOWN_SECONDS` / `MAIL_MAX_PER_ADDRESS_DAILY` | agent_subscriptions | Pacing of confirmation and link emails per address (defaults `60` / `5`) |
| `MAIL_DAILY_CEILING` | agent_subscriptions | Daily ceiling on those emails (default `1000`); set a very high number to switch it off |
| `ADMIN_TOKEN` | agent_subscriptions | Token for `GET /stats` (send as `Authorization: Bearer`); the route is off while unset. The home page uses the public `GET /spots` instead |
| `ALERT_EMAIL` | agent_healthcheck | Where the draft and send health checks email their reports; never used for subscriber-facing sends |
| `GOOGLE_OAUTH_CLIENT_ID` | agent_subscriptions | Google OAuth 2.0 Web client ID for server-side Google Sign-In (not secret) |
