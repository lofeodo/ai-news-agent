# Configuration

Credentials (API keys and similar) are supplied to the services at runtime and are never committed to this repo. The table below lists the non-secret configuration.

| Variable | Used by | Purpose |
|---|---|---|
| `USE_FIRESTORE` | all agents | Enable cloud mode (Pub/Sub + Firestore); default `false` |
| `GCP_PROJECT_ID` | all agents | Google Cloud project ID |
| `AGENT1B_MODE` | agent1b | `graph` (default, LangGraph) or `single_pass` (original linear code; rollback switch) |
| `AGENT1A_MODE` / `AGENT1A_FULLTEXT_TOP_K` | agent1a | `full` (default, score every PDF) or `abstract_first` (score abstracts, then the full text of the top K, default 8; about half the cost). Read on every run, so changing it needs no redeploy |
| `AGENT2B_MODE` | agent2b | `all` (default, summarize every article) or `shortlist` (summarize only what a first selection pass keeps; cuts ~65% of agent2b's cost, see the Step 3 notes in `cost-reduction-roadmap.md`). Read on every run, so changing it needs no redeploy |
| `REVIEW_CONFIDENCE_THRESHOLD` / `REVIEW_MAX_ARTICLES` / `REVIEW_MAX_ITERATIONS` / `REVIEW_FETCH_TIMEOUT` | agent1b | Review-loop tuning (defaults `4` / `0` / `3` / `10`; review is off by default, `REVIEW_MAX_ARTICLES=30` turns it on); see [Inside agent 1b](architecture.md#inside-agent-1b-langgraph) |
| `LANGSMITH_TRACING` / `LANGSMITH_PROJECT` | all Claude-calling agents | Opt-in LangSmith tracing; a no-op without an API key |
| `CLICK_TRACKING` | agent4 | `true` turns on SendGrid click tracking and tags each email with the run id (default off) |
| `TRENDING_LOOKBACK_DAYS` | agent1a | Days of Hugging Face Daily Papers considered for the shortlist (default `14`; a constant in `config.py`) |
| `JUDGE_MAX_ITEMS` / `JUDGE_MAX_USD` | healthcheck | Summaries judged per weekly run and its hard cost cap (constants in `config.py`) |
| `AGENT_NAME` | main.py | Selects which agent the Cloud Run container runs |
| `TOPIC` | local runs | Which topic profile the local agents build (`python orchestrator.py --topic finance` sets it); default `ai`. In cloud mode every agent reads the topic from the run doc instead |
| `TEST_RECIPIENT_EMAIL` | agent4 | Local mode: single send address |
| `TEST_SEND_TO` | agent4 | Cloud mode override: skip subscriber list, send only here |
| `SERVICE_BASE_URL` | agent4, agent_subscriptions | Public URL of the subscription API service |
| `FRONTEND_BASE_URL` | agent3, agent4, agent_subscriptions | Public URL of the Firebase Hosting frontend — must be `https://newsletter.lofeodo.com` on every service that sets it; a mismatch here silently breaks the `{{PREFERENCES_URL}}` link in every sent newsletter |
| `ALLOWED_ORIGINS` | main.py (subscriptions) | Comma-separated CORS origins; required in production |
| `MAILING_ADDRESS` | agent3 | Physical address in email footer (CASL compliance) |
| `MAX_SUBSCRIBERS` | agent_subscriptions | Subscriber cap (default `40000`); counts people, not subscriptions (`active` plus `subscribed_any`) |
| `PUBLIC_TOPICS` | agent_subscriptions | The launch switch: comma-separated topic ids anyone may see and subscribe to (`ai` is always public; unknown ids are ignored and logged). Unset = only AI. Other topics stay hidden, visible to `ADMIN_EMAILS` only. Read per request, so `--update-env-vars` launches a topic without new code (see the topic launch checklist in `runbook.md`) |
| `RESPONSE_FLOOR_SECONDS` / `SENDGRID_TIMEOUT_SECONDS` | agent_subscriptions | Minimum response time of the mail routes, and the timeout of outgoing mail calls (defaults `2.0` / `8`) |
| `MAIL_COOLDOWN_SECONDS` / `MAIL_MAX_PER_ADDRESS_DAILY` | agent_subscriptions | Pacing of confirmation and link emails per address (defaults `60` / `5`) |
| `MAIL_DAILY_CEILING` | agent_subscriptions | Daily ceiling on those emails (default `1000`); set a very high number to switch it off |
| `ADMIN_TOKEN` | agent_subscriptions | Token for `GET /stats` (send as `Authorization: Bearer`); the route is off while unset. The home page uses the public `GET /spots` instead |
| `ALERT_EMAIL` | agent_healthcheck | Where the draft and send health checks email their reports; never used for subscriber-facing sends |
| `GOOGLE_OAUTH_CLIENT_ID` | agent_subscriptions | Google OAuth 2.0 Web client ID for server-side Google Sign-In (not secret) |

## Topic profiles

One pipeline writes any number of newsletters. Everything that differs between them lives in `topics/<id>.yaml`, loaded and validated by `agents/topics.py` (a bad profile fails loudly, see `tests/test_topics.py`). `topics/ai.yaml` is the original AI newsletter and is pinned to the old constants by `tests/test_topics_ai_equivalence.py`; the other profiles are `finance`, `gaming`, `boardgames` and `printing3d`.

| Field | Meaning |
|---|---|
| `id`, `name`, `tagline`, `audience` | Identity. `name` and `tagline` head the email; `audience` is one sentence used in the prompts |
| `accent_color` | `#rrggbb` used for the header, section numbers and links (links on white cards use a darker shade) |
| `languages`, `variants` | `[en]` and `["0_0"]` for the new topics. The AI newsletter keeps `en`/`fr` and its four variants |
| `spotlight` | `papers` (the AI newsletter's weekly paper) or `none`. With `none`, agent1a and agent2a record an empty stage and still count and publish, so the chain is unchanged |
| `max_articles` | Cap after the prefilter (default 600, `0` = none). Hacker News stories and curated RSS articles are kept first; the NewsAPI long tail is cut first |
| `categories` | 1 to 7, in newsletter order: `name`, `definition` (given to the categorize prompt), optional `cap` (articles per section, default 3), `icon`, and `variant_flag` (AI only: hides the Canada section from variants without it) |
| `sources` | `hn` (no `queries` = the week's top stories, as the AI newsletter does; otherwise each query is searched separately, with `min_points`), `newsapi` (`queries`, 500 characters each at most) and `rss` (`feeds`: `name`, `url`, optional `max_items`). A dead feed is logged and skipped, never fatal |
| `paywalled_domains` | Added to the global list in `config.py` |
| `guidelines` | Extra editorial guidance pasted into the categorize and selection prompts; the cheapest place to tune a topic's quality |
| `disclaimer` | One line in the email footer (finance uses it) |
| `prompts` | Optional paths overriding the generic prompts in `prompts/generic/` (`news_filter`, `selection`, `intro`, `news_summary`, `news_summary_fallback`). The AI profile names its original files |

**Add a topic:** copy a profile, change the fields, check every RSS URL returns recent items (a feed with nothing from the last week adds nothing), and run `python orchestrator.py --topic <id>` locally. Nothing else needs registering: the orchestrator, the admin topic picker and the tests discover profiles from the folder.

**Release vs debug.** Only the AI newsletter can be a release run; the orchestrator refuses `kind=release` for any other topic (400). Every "latest run" lookup (agent4, the public pages, the archive, the health check, the drift baseline) ignores other topics' runs, whatever their kind.
