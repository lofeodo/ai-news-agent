# Design decisions

Why the system is built the way it is. For the overview see the [README](../README.md).

**Event-driven fan-in.** Agents 2a and 2b run in parallel (both triggered by their respective Pub/Sub messages). A Firestore atomic transaction increments `agent2_completions`; the agent that pushes the count to 2 publishes `content-summarized`. This avoids a coordinator process and handles the race condition correctly under concurrent Cloud Run instances.

**ArXiv proxy.** GCP datacenter IPs are rate-limited or blocked by ArXiv's CDN. Requests go through an HTTP proxy configured with the standard proxy environment variables; both the `urllib` opener and the `arxiv` library's internal `requests.Session` are patched to use it.

**No CPU throttling on pipeline agents.** Cloud Run's default "CPU only allocated during request" would pause the background thread immediately after the HTTP response is returned. Pipeline agents use `--no-cpu-throttling` so the thread runs to completion. Synchronous services (agent4, subscriptions) don't need this.

**Hard runtime watchdog.** `main.py` arms a `threading.Timer` around every agent: whichever is sooner of 1 hour after start, or 07:30 America/Toronto for a run that started before it (a manual daytime recovery run only gets the 1-hour cap). On expiry it records `{agent}_error`/`{agent}_failed_at` to the run's Firestore doc, then `os._exit(124)` — a hard process exit that works even if the agent thread is wedged in a C extension, which a Python-level timeout wouldn't survive. Added after agent2b died from a native SIGABRT with no trace left behind; the watchdog bounds hangs going forward, though it can't help a process that has already crashed on its own.

**Dual auth model.** The subscription service supports two auth paths. The original "inbox as auth" token model (email links) remains fully functional for newsletter footer links and legacy subscribers. A new account-based path uses Firebase Authentication (Google OAuth): the frontend gets a Firebase ID token and sends it as `Authorization: Bearer`; `auth_middleware.py` verifies it with `firebase-admin`. Account-based subscribers get immediate subscribe/unsubscribe/preferences without waiting for an email — the Firebase auth flow already verified inbox ownership. Both paths read and write the same `subscribers` Firestore collection; account subscribers get a `uid` field linking them to the `users` collection.

**Email deliverability.** Mail sends from `newsletter@lofeodo.com` via SendGrid with full domain authentication (DKIM + SPF via CNAME records, DMARC policy). Sending from a gmail.com address through a third-party relay fails SPF alignment and lands in spam — a controlled sending domain is required.

**Soft delete.** Unsubscribing sets `active: false`; the document is never deleted. This preserves the audit trail and allows re-subscription without losing history.

**Subscriber variants.** Agent 3 generates four newsletter HTML variants keyed by `{include_french}_{include_canada}` (`0_0`, `1_0`, `0_1`, `1_1`). Agent 4 picks the correct variant per subscriber at send time, so no re-rendering is needed per send.

**Failure recording over silent stalls.** Every pipeline agent's top-level exception is caught and recorded to its `pipeline_runs` document (`{agent}_error`, `{agent}_failed_at`) before re-raising, rather than only surfacing in Cloud Logging. Without this, one agent failing partway through leaves the run permanently incomplete with no durable trace of why — the standalone health check agent depends on these fields being present to report a specific cause rather than just "something didn't finish." See `CLAUDE.md` for the full mechanics, including a known limitation: the health check's own alert email shares SendGrid with the real newsletter send, so a SendGrid-specific outage can suppress the alert about the very failure it's meant to catch.
