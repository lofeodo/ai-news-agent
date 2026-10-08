# Repository structure

```
.
├── agents/
│   ├── agent1a_fetch_papers.py     # ArXiv fetch + Claude scoring
│   ├── agent1b_fetch_news.py       # HN + NewsAPI fetch, language filter, categorize
│   ├── agent2a_summarize_papers.py # PDF download + Claude paper reviews
│   ├── agent2b_summarize_news.py   # Article fetch + Claude news summaries
│   ├── agent3_compose.py           # Article selection, intro, HTML composition
│   ├── agent4_send.py              # Per-subscriber personalization + SendGrid send
│   ├── agent_healthcheck.py        # Standalone health check + alert (draft check Sunday, send check Monday)
│   ├── agent_subscriptions.py      # Subscription FastAPI service (separate deployment)
│   ├── auth_middleware.py          # Firebase ID token verification (FastAPI dependency)
│   ├── agent1b_graph.py            # LangGraph implementation of agent1b (state, nodes, review loop)
│   ├── article_fetch.py            # Shared article-text fetcher (agent1b review + agent2b)
│   ├── trending_papers.py          # Hugging Face Daily Papers shortlist for agent1a
│   ├── spotlight_history.py        # Papers already spotlighted (Firestore / local file)
│   ├── summary_sources.py          # Saves the text each summary was written from (for the judge)
│   ├── judge.py / online_judge.py  # Summary-faithfulness judge and its weekly sampler (report-only)
│   ├── drift.py / drift_history.py # Drift tests on agent1b output, token usage and judge rates
│   ├── tracing.py / usage_archive.py / pricing.py  # LangSmith tracing, weekly usage archive, cost
│   ├── click_links.py / click_counts.py / click_report.py / sendgrid_webhook.py  # Click signal
│   ├── prompt_guard.py             # Guard text and sanitising for untrusted content in prompts
│   ├── report_html.py              # Renders the health check report as newsletter-styled HTML
│   ├── filter_tool.py              # Claude tool schemas for news categorization + review
│   └── scoring_tool.py             # Claude tool schema for paper scoring
├── prompts/
│   ├── scoring_rubric.txt          # 8-dimension paper scoring prompt
│   ├── judge_prompt.txt            # Summary-faithfulness judge prompt
│   ├── paper_summary_prompt.txt    # Paper mini-review prompt
│   ├── news_filter_prompt.txt      # News categorization prompt
│   ├── news_filter_confidence_addendum.txt  # Adds the 1-5 confidence rubric (graph mode)
│   ├── news_review_prompt.txt      # Agent 1b review-loop prompt
│   ├── news_summary_prompt.txt     # News article summary prompt
│   ├── news_summary_fallback_prompt.txt
│   ├── article_selection_prompt.txt
│   ├── intro_prompt.txt            # Editor's note prompt
│   └── quebec_french_style.txt     # French-language style guide for news summaries
├── public/newsletter/              # Firebase Hosting frontend
│   ├── index.html                  # Subscribe form (auth-aware nav)
│   ├── login.html                  # Sign in (Google) + emailed preferences link
│   ├── preferences.html            # Preferences (account auth or token fallback)
│   ├── unsubscribe.html            # Unsubscribe (one-click if signed in, email form otherwise)
│   ├── preview.html                # Newsletter preview page
│   ├── sections.html               # Premium newsletter-sections customization UI
│   ├── auth-callback.html          # Google Sign-In exchange-code redemption landing page
│   ├── auth.js                     # Shared Firebase Auth helper (ES module)
│   ├── nav.js                      # Shared auth-aware navigation bar
│   ├── bg.js                       # Shared background/decorative script
│   ├── style.css / fonts.css       # Shared styling
│   └── fonts/, images/             # Static assets
├── evals/                          # Evaluation harnesses, frozen fixtures, labels, results/*.json (see evals/README.md)
├── tests/                          # pytest suite (stubbed Claude client + fetcher; no network or keys)
│   ├── conftest.py / fakes*.py     # Path setup; scripted fake Anthropic client, fetcher and Firestore
│   └── test_*.py                   # agents, graph, evals, drift, judge, click signal, injection defences
├── docs/
│   ├── architecture.md             # Stage-by-stage detail, orchestration, subscription system
│   ├── design-decisions.md         # Why the system is built the way it is
│   ├── monitoring.md               # Health check, drift, cost, judge, click signal
│   ├── evaluation.md               # Review-loop and prompt-injection evaluation results
│   ├── configuration.md            # Non-secret configuration reference
│   ├── development.md              # Local development and deployment
│   ├── repository-structure.md     # This file
│   ├── plans/                      # Implementation plans (e.g. langgraph-agent1b.md)
│   ├── decisions/                  # Architecture decision records (ADRs)
│   └── history/                    # Archived working log of the evaluation and monitoring work
├── .github/workflows/tests.yml     # CI: pytest on push and pull request (no secrets)
├── selection_test.py               # Manual script (real Claude calls) — NOT collected by pytest
├── pytest.ini                      # Restricts pytest to tests/
├── orchestrator.py                 # Local sequential runner / cloud pipeline trigger
├── main.py                         # Cloud Run entrypoint (FastAPI, AGENT_NAME dispatch)
├── config.py                       # Shared constants and env var reads
├── Dockerfile                      # Single image, AGENT_NAME build arg
├── cloudbuild.yaml                 # Cloud Build: build + push all 9 service images
├── cloudbuild-partial.yaml         # Cloud Build: agent1b + agent3 + agent4 only (fast iteration)
├── cloudbuild-subscriptions.yaml   # Cloud Build: agent_subscriptions only
├── firebase.json                   # Firebase Hosting config
├── firestore.indexes.json          # Firestore composite index definitions
├── requirements.txt
├── requirements-dev.txt            # requirements.txt + pytest and eval dependencies
```
