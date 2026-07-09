# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Browser Testing

After any UI change, test on all three environments:

1. **Desktop** — use `playwright`
2. **Android** — use `playwright-mobile-android` (Pixel 7 emulation)
3. **iOS** — use `playwright-mobile-ios` (iPhone 15 emulation)

Take a screenshot in each after changes. Flag any layout, overflow, or interaction issues specific to mobile viewports.

## Git Workflow

**Never commit or push unless explicitly asked by the user.**

When asked to commit: create incremental commits with individual changes — one commit per meaningful unit (e.g., add CSS, update one page, create a new file), not one big commit at the end of a feature. Push only when explicitly asked.

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
- `GET /auth/me` — return user info + subscription status + tier.
- `POST /auth/subscribe` — subscribe instantly (email already verified; requires `email_verified: true`). Accepts `{"send_latest": bool}` body.
- `POST /auth/unsubscribe` — deactivate subscription.
- `GET /auth/google/login?return_to=` — start Google Sign-In (see "Google Sign-In" below).
- `GET /auth/google/callback` — Google OAuth redirect target.
- `POST /auth/google/exchange` — redeem a one-time exchange code for a Firebase custom token.
- `GET /auth/preferences` — return prefs + tier.
- `POST /auth/preferences` — update prefs.
- `POST /auth/sections/refine` — **premium only**; takes `{"raw_topic": "SpaceX"}`, calls Claude Haiku, returns `{"refined_topic": "SpaceX product launches & mission updates"}`. Rate-limited 5/min.
- `GET /auth/sections` — **premium only**; returns `{default_sections, section_config}`.
- `POST /auth/sections` — **premium only**; saves user's section configuration.

**Subscriber doc fields:** `email`, `token`, `token_expires_at`, `active`, `subscribed_at`, `confirmed_at`, `prefs: {include_french, include_canada}`, `send_latest`, `latest_sent`, `uid` (Firebase UID, null for legacy subscribers). Token TTL: 48h for confirmation, 365d for action links.

**Firestore collections:** `subscribers` (existing), `users` (doc ID = Firebase UID, fields: `email`, `display_name`, `provider`, `created_at`, `tier`, `section_config`), `oauth_states` (transient, ~10min TTL, Google Sign-In CSRF state), `oauth_exchange_codes` (transient, ~60s TTL, one-time Google Sign-In handoff — see below).

### Account Tiers

Two tiers: `"free"` (default) and `"premium"`. Tier is set at login time based on the `PREMIUM_EMAILS` env var (comma-separated emails). Premium unlocks the Newsletter Sections customization UI on `preferences.html`.

**Section config schema** (stored in `users/{uid}.section_config`):
```json
{
  "enabled_sections": ["Model & Product Releases", "Industry & Business", ...],
  "custom_sections": [
    { "id": "abc123", "raw_input": "SpaceX", "refined_topic": "SpaceX product launches & mission updates" }
  ]
}
```
`enabled_sections: null` means all default sections are enabled (the default). The `DEFAULT_SECTIONS` list (canonical order) is defined in `agents/agent_subscriptions.py`.

**Future work — custom section article sourcing:** Custom sections currently store the topic preference but do not yet fetch articles. The planned approach is to use Claude's web search tool (`web_search`) in the pipeline to retrieve relevant articles for each subscriber's custom sections, then include them as additional newsletter sections. This is a pipeline-level change (agent1b or a new agent1c) tracked as a follow-up.

`main.py` conditionally mounts the subscription router when `AGENT_NAME=agent_subscriptions`. CORS `allow_headers` includes `Authorization` for the account-based routes.

### Firebase Auth Setup (one-time, manual)

In Firebase Console → Authentication → Sign-in method:
1. Enable **Google** provider
2. Enable **Email/Password** (standard, not email link)
3. Add authorized domains: `newsletter.lofeodo.com`, `latentspacemail.web.app`

`auth.js` loads the Firebase project config automatically from `/__/firebase/init.json`, which Firebase Hosting serves on all deployments. **No API key in source.** For local frontend development with auth, run `firebase serve --only hosting` instead of a plain HTTP server (plain servers don't serve that endpoint).

**Important — authDomain override:** `auth.js` overrides `authDomain` to `window.location.hostname` on production. This now matters only for Firebase's hosted `/__/auth/action` pages (password reset / email verification links — the email/password flow is still client-side Firebase Auth). `https://newsletter.lofeodo.com/__/auth/handler` remains registered in the Google OAuth 2.0 client's authorized redirect URIs from the old Google flow (see below); it's unused now but harmless to leave registered.

**Google Sign-In: server-side OAuth Authorization Code flow.** Google Sign-In on mobile Safari was broken across 9 client-side Firebase SDK attempts spanning two branches (popup-first, redirect-only, UA-sniffed popup-vs-redirect, every combination of `initializeAuth`/`getAuth`/persistence/`popupRedirectResolver` — see `git log` on `fix/google-signin-authdomain` and `fix/safari-auth-initialize` for the full history). The likely root cause was architectural, not a config error: Firebase's redirect flow depends on writing "a redirect is pending" state to IndexedDB immediately before navigating to `accounts.google.com` and reading it back via `getRedirectResult()` after the round trip — exactly the kind of storage Safari's Intelligent Tracking Prevention (ITP) partitions/evicts around cross-site navigation bounces.

The fix moves the entire OAuth negotiation server-side (`agents/agent_subscriptions.py`), and the client SDK never calls `signInWithPopup`/`signInWithRedirect`/`getRedirectResult` at all:
1. `login.html`/`index.html`'s "Continue with Google" is a plain `<a href="{SERVICE_BASE_URL}/auth/google/login?return_to=...">` — a real navigation, not an SDK call, so there's no client-side error path for *starting* sign-in.
2. `GET /auth/google/login` sets a CSRF `state` cookie + Firestore doc, redirects to Google's consent screen.
3. `GET /auth/google/callback` — Google redirects back here. Verifies the `state` cookie, exchanges the code for tokens server-to-server (`requests` + `google.oauth2.id_token.verify_oauth2_token`), gets-or-creates the Firebase user via Admin SDK, and redirects to `auth-callback.html` with a short-lived (60s) single-use exchange code.
4. `auth-callback.html` POSTs the exchange code to `POST /auth/google/exchange`, receives a Firebase custom token, and calls `signInWithCustomToken()`.

**Important — `create_custom_token` needs a signing-capable identity, unlike everything else this app does with Firebase Admin.** `verify_id_token` (used by every other `/auth/*` route) works fine with plain Application Default Credentials. `create_custom_token` (step 3 above, the only caller in this codebase) additionally needs to *sign* a JWT, which ADC alone can't do:
- **On Cloud Run**, the Admin SDK discovers the attached service account via the metadata server and signs remotely via the IAM Credentials API — but only if that service account has been granted `roles/iam.serviceAccountTokenCreator` **on itself**. This is not covered by `roles/editor`. One-time setup: `gcloud iam service-accounts add-iam-policy-binding <SA_EMAIL> --member="serviceAccount:<SA_EMAIL>" --role="roles/iam.serviceAccountTokenCreator"`. Without this, `/auth/google/exchange` 500s with `ValueError: Failed to determine service account`.
- **Locally**, there's no metadata server at all, so `gcloud auth application-default login` isn't sufficient on its own — `create_custom_token` fails immediately. Fix: download a service account key JSON (`gcloud iam service-accounts keys create key.json --iam-account=<SA_EMAIL>`) and set `GOOGLE_APPLICATION_CREDENTIALS` to its path before starting the backend; the key's embedded private key signs locally with no IAM call needed. Keep the key out of git (already covered by the existing `.env`/`*.env` gitignore pattern if stored alongside it, but the key file itself is not a `.env` file — gitignore it explicitly too).

The only "did sign-in succeed" signal the frontend relies on is a URL query parameter attached to an HTTP redirect — not IndexedDB, localStorage, cookies, or `window.name` — so ITP's storage-partitioning model doesn't apply to it. Every hop is either a same-tab top-level redirect or a same-origin `fetch`; there's no popup and no `window.opener` anywhere, so the original `signInWithPopup` failure mode is structurally impossible rather than merely avoided by configuration. `auth.js`'s `initializeAuth` no longer needs `popupRedirectResolver` — it was only required for `signInWithRedirect`/`signInWithPopup`/`getRedirectResult`, none of which remain in the app.

Known accepted residual risk: a narrow replay window exists between step 3's redirect and step 4's (automatic, sub-second) redemption of the exchange code. Closing it would require binding that hop to a cookie too, but the frontend and backend are different sites (`newsletter.lofeodo.com` vs `*.a.run.app`), so that cookie would necessarily be cross-site — exactly the kind of storage Safari's ITP restricts, reintroducing the fragility this rebuild exists to eliminate. Judged not worth it for v1; revisit only if abuse is observed.

**Important — Firebase SDK version:** Use `10.14.1` from the CDN only. SDK 12.x has a runtime initialization error on iPadOS Safari that silently aborts the entire `<script type="module">` block — this constraint governs the remaining email/password flow (`signInWithEmailAndPassword`, `signInWithCustomToken`, etc.) and hasn't been re-tested since it's orthogonal to the Google Sign-In rebuild.

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
| `PREMIUM_EMAILS` | Comma-separated emails that get `tier: "premium"` on login (e.g. `daniel.lofeodo@gmail.com`) |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth 2.0 Web client ID for server-side Google Sign-In (not secret) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth 2.0 client secret (or use `USE_SECRET_MANAGER=true`, secret name `google-oauth-client-secret`) |

## Pub/Sub Topics (Cloud Mode)

`pipeline-start` → `papers-scored` + `news-filtered` → `content-summarized` → (agent3 runs) → agent4 triggered separately by Cloud Scheduler.

## Prompts

All Claude prompts are in `prompts/`. Edit prompt files to change scoring behavior, summary style, or category definitions without touching Python code.
