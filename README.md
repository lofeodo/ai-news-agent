# Latent SpaceMail

<div align="center">

<a href="https://newsletter.lofeodo.com"><img src="docs/assets/banner.svg" alt="Latent SpaceMail: a weekly AI briefing, written and sent by a team of AI agents. Live site: newsletter.lofeodo.com" width="100%"></a>

<a href="https://github.com/lofeodo/ai-news-agent/actions/workflows/tests.yml"><img src="https://github.com/lofeodo/ai-news-agent/actions/workflows/tests.yml/badge.svg" alt="CI status"></a>
<img src="https://img.shields.io/badge/python-3.11-3776ab?logo=python&logoColor=white" alt="Python 3.11">
<img src="https://img.shields.io/badge/Google_Cloud_Run-4285f4?logo=googlecloud&logoColor=white" alt="Google Cloud Run">
<img src="https://img.shields.io/badge/LangGraph-1c3c3c?logo=langchain&logoColor=white" alt="LangGraph">
<img src="https://img.shields.io/badge/Claude-d97757?logo=anthropic&logoColor=white" alt="Anthropic Claude">
<img src="https://img.shields.io/badge/SendGrid-1a82e2?logo=twilio&logoColor=white" alt="SendGrid">
<img src="https://img.shields.io/badge/Firebase-ffca28?logo=firebase&logoColor=black" alt="Firebase">

</div>

A weekly AI briefing, written and sent by a team of AI agents: one spotlight research paper plus the week's top AI news, delivered as a personalized email.

- **Agentic orchestration**: six agents on Google Cloud, coordinated by Pub/Sub, plus a LangGraph review loop inside one of them.
- **Live monitoring**: health checks, drift detection, cost tracking and an LLM judge report on every run.
- **Measured, not claimed**: evaluation results are generated from scripts, with confidence intervals.
- **Real cloud operations**: Cloud Run, Cloud Build, GitHub Actions CI, secrets management and no-redeploy rollback switches.

## How it works

Stage-by-stage detail: [docs/architecture.md](docs/architecture.md)

```mermaid
%%{init: {"themeVariables": {"fontSize": "16px"}, "flowchart": {"nodeSpacing": 26, "rankSpacing": 40, "padding": 10, "curve": "basis"}}}%%
flowchart LR
    ORC["☁️ Sun 12 PM<br/><b>Orchestrator</b>"] --> A1A["<b>Agent 1a</b><br/>Pick paper"]
    ORC --> A1B["<b>Agent 1b</b><br/>Fetch news"]
    A1A --> A2A["<b>Agent 2a</b><br/>Summarize"]
    A1B --> A2B["<b>Agent 2b</b><br/>Summarize"]
    A2A --> FAN{"<b>Fan-in</b><br/>2 of 2"}
    A2B --> FAN
    FAN --> A3["<b>Agent 3</b><br/>Compose"]
    A3 --> DB[("Firestore<br/>run state")]
    DB --> A4["☁️ Mon 7 AM<br/><b>Agent 4</b> · Send"]
    DB --> HC1["☁️ Sun 1:15 PM<br/><b>Draft check</b>"]
    DB --> HC2["☁️ Mon 7:10 AM<br/><b>Send check</b>"]

    classDef agent fill:#4f46e5,stroke:#312e81,color:#fff
    classDef fan fill:#7c3aed,stroke:#4c1d95,color:#fff
    classDef store fill:#d97706,stroke:#92400e,color:#fff
    classDef health fill:#059669,stroke:#065f46,color:#fff
    class ORC,A1A,A1B,A2A,A2B,A3,A4 agent
    class FAN fan
    class DB store
    class HC1,HC2 health
```

| Agent | What it does |
|---|---|
| **1a** Papers | Picks the week's spotlight paper from Hugging Face's trending list and scores it with Claude. |
| **1b** News | Gathers Hacker News and NewsAPI stories, filters by language and sorts them into 7 categories. |
| **2a / 2b** Summaries | Write a short summary of the paper (2a) and of each article (2b), in parallel. |
| **3** Compose | Picks the best articles per category, writes the editor's note and builds the HTML email. |
| **4** Send | Sends each subscriber their personalized version through SendGrid on Monday morning. |
| **Health checks** | A draft check on Sunday and a send check on Monday email a report on every run. |

## Agentic orchestration

- **Between agents:** Pub/Sub events plus a Firestore counter that joins agents 2a and 2b before agent 3 starts.
- **Inside an agent:** agent 1b is a LangGraph graph that sends low-confidence articles to a small tool-using review loop.
- **Failure handling:** every agent records its errors to Firestore, a watchdog kills hung runs, and the crash-prone agent 2b retries in an isolated process.
- **Rollback:** `AGENT1B_MODE=single_pass` restores the original linear code without a redeploy.

```mermaid
%%{init: {"themeVariables": {"fontSize": "16px"}, "flowchart": {"nodeSpacing": 28, "rankSpacing": 44, "padding": 10, "curve": "basis"}}}%%
flowchart LR
    COL["<b>Collect</b><br/>HN + NewsAPI<br/>drop paywalls"] --> LANG["<b>Language</b><br/>English or<br/>French only"]
    LANG --> CAT["<b>Categorize</b><br/>7 topics +<br/>confidence 1-5"]
    CAT -->|"confident"| FIN["<b>Finalize</b><br/>write results<br/>+ audit log"]
    CAT -->|"unsure"| LLM["<b>Review: think</b><br/>Claude decides"]
    LLM -->|"needs text"| TOOL["<b>Review: act</b><br/>fetch the article"]
    TOOL --> LLM
    LLM -->|"answer"| FIN

    classDef step fill:#4f46e5,stroke:#312e81,color:#fff
    classDef loop fill:#7c3aed,stroke:#4c1d95,color:#fff
    classDef out fill:#059669,stroke:#065f46,color:#fff
    class COL,LANG,CAT step
    class LLM,TOOL loop
    class FIN out
```

Why LangGraph runs inside agents but not between them: [ADR 0001](docs/decisions/0001-langgraph-inside-agents.md).

## Live monitoring

Every weekly run ends in an email report to the maintainer, whether or not anything is wrong, so a missing email is itself a signal.

| Signal | What it watches | Response |
|---|---|---|
| **Health checks** | Every pipeline stage, the composed newsletter and delivery | Email report: all clear or problem detected |
| **Failure recording and watchdog** | Agent errors and hung or crashed runs | Written to Firestore; hung runs are killed |
| **Drift** | Agent 1b's confidence, category mix and review rate | Flagged only for large, significant shifts |
| **Token and cost** | LangSmith token and cost totals per run | Flagged on big jumps versus prior weeks |
| **Summary judge** | A weekly sample of summaries checked against their sources | Report only (see Results) |
| **Click signal** | Aggregate link clicks, with no subscriber data | Informational section in the report |

Details: [docs/monitoring.md](docs/monitoring.md)

## Cloud, CI and operations

| Area | What's in place |
|---|---|
| **Cloud** | 10 Cloud Run services from one Docker image, Pub/Sub, Firestore, Cloud Scheduler, Secret Manager, Firebase Hosting and Auth |
| **Build and deploy** | Cloud Build builds every image in parallel; deploys are separate, deliberate steps; API keys are mounted from Secret Manager |
| **CI** | GitHub Actions runs the full test suite (stubbed Claude client, no keys) and a Docker build check on every push and pull request |
| **Evaluation** | Paid evals run on demand behind a cost estimate and a hard cap; results are JSON files with confidence intervals |
| **Observability** | LangSmith tracing, the weekly health check report, structured logs |
| **Reliability** | Idempotent fan-in, isolated retries, a 24-hour stale-newsletter guard, rollback switches |
| **Stack** | Python 3.11, Anthropic Claude, LangGraph, FastAPI, SendGrid, Firebase Auth |

Deployment and local setup: [docs/development.md](docs/development.md)

## Results

All charts below are generated from `evals/results/*.json` by `python -m evals.make_readme_table`, never drawn or typed by hand. Each has its table in [docs/evaluation.md](docs/evaluation.md).

### Does the review loop help?

Agent 1b's review loop was compared with the original single-pass code on the same hand-labeled articles. It did not measurably help: the differences sit inside the noise, and the model's own confidence did not predict its mistakes. Reported plainly.

<img src="docs/assets/chart-review-accuracy.svg" alt="Bar chart: category accuracy of single-pass, graph first pass and graph after review, with overlapping 95% intervals" width="100%">

### Prompt-injection tests

Hand-written attacks (forced categories, tag breakouts, prompt leaks, planted URLs) were run through the real agent code paths, once with the injection and once without. The attacks that worked before the fixes were stopped by input sanitising, guard text on every Claude call and a fetch guard. The checks catch canary-style compliance only, and small samples mean "none observed" is not "safe".

<img src="docs/assets/chart-injection.svg" alt="Bar chart: prompt-injection attack success before and after the fixes, with control" width="100%">

### Summary judge

A second Claude model checks a weekly sample of summaries against the text they were written from. Calibrated against 40 summaries I labeled by hand, it agreed with me no better than chance (kappa in the table), so it runs report-only and can never trigger an alert.

<img src="docs/assets/chart-judge-kappa.svg" alt="Kappa of the summary judge against the working bar" width="100%">

Full results and caveats: [docs/evaluation.md](docs/evaluation.md)

## Documentation

- [Architecture](docs/architecture.md): stage-by-stage detail, orchestration and the subscription system
- [Design decisions](docs/design-decisions.md): why the system is built this way
- [Monitoring](docs/monitoring.md): health checks, drift, cost, judge and click signal
- [Evaluation](docs/evaluation.md): full review-loop and prompt-injection results
- [Configuration](docs/configuration.md): environment variables
- [Local development and deployment](docs/development.md)
- [Repository structure](docs/repository-structure.md)
- [Evaluation harness conventions](evals/README.md)
