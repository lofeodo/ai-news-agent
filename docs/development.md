# Local development and deployment

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

**Run the tests (no network, no API keys):**
```bat
venv\Scripts\python -m pip install -r requirements-dev.txt
venv\Scripts\python -m pytest -q
```
CI (`.github/workflows/tests.yml`) runs the same on every push and PR. `pytest.ini` limits collection to `tests/`: the root-level `selection_test.py` is a manual script that makes real Claude calls when imported, so it must never be collected. Whether CI blocks a merge is a GitHub branch-ruleset setting ("Require status checks to pass" with the `pytest` check), not something this repo's files enforce.

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
  --set-env-vars AGENT_NAME=agent1a,USE_FIRESTORE=true,...    # non-secret config only
```

`--set-*` is fine on a first deploy, but on an existing service use `gcloud run services update ... --image IMAGE` to ship new code: it never prompts, never creates a second service, and leaves all env vars and secrets untouched.

The subscription service and agent4 (sender) are synchronous and don't need `--no-cpu-throttling`.
