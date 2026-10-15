# Runbook

How to diagnose the weekly health check email, re-run a stage, roll back a change and rotate a key. Every procedure here was checked against `main.py`, `agents/agent_healthcheck.py` and `cloudbuild.yaml`. Incident history is in [postmortems/](postmortems/README.md).

Schedule (America/Toronto): pipeline drafts Sunday 12:00 PM, draft health check Sunday 1:15 PM (did it compose properly: stages through agent3, all four variants, placeholders, send-day date), agent 4 send Monday 7:00 AM, send health check Monday 7:10 AM (did it send). The issue is dated the Monday send date. The watchdog kills an agent after 1 hour, or at 07:30 if it started before then (a Sunday-noon run only gets the 1-hour cap). A problem in the Sunday email leaves the whole day to fix and re-run before the send.

Commands use PowerShell syntax; `gcloud` works from PowerShell on the owner's machine, not Git Bash. Project and region come from the gcloud config. Replace `<run_id>` and `<SERVICE>`; never paste real secret values or unredacted `describe` output into a ticket, a commit or a chat.

## 1. Read the health check email

One email arrives every Monday. The subject says "all clear" or "problem detected". No email at all is itself a failure (see "No email arrived").

Only the first list below changes the subject. The drift, usage, judge and click sections are informational and never change it.

| Message in the email | What it means | First check |
|---|---|---|
| `No pipeline_runs document found at all` | Firestore has no run docs. | Did the orchestrator service run? Check Cloud Scheduler and the orchestrator logs. |
| `No recent pipeline run found … started Nh ago` (N over 4) | The newest run doc is old, so this week's pipeline never started or never wrote its start. | Scheduler job, orchestrator logs, then Pub/Sub `pipeline-start`. |
| `<agent> failed at <time>: <error>` | That agent caught an exception and recorded it. | The error text, then that service's logs for the same `run_id`. |
| `… hard timeout — … exceeded its deadline` | The watchdog killed the agent (hang, or it was still running at 07:30). | Logs for the stage; re-run it (section 2). |
| `crashed (killed by SIGABRT) on all 6 attempts` | agent 2b died by signal on every attempt. The root cause of this crash is unconfirmed. | agent 2b logs for `[main] agent2b attempt n/6`; re-run. See the [2026-09-07 postmortem](postmortems/2026-09-07-stale-newsletter.md). |
| `<stage> never completed — '<field>' missing` | The stage did not write its output, with no error. This is how a native crash looks. | Find the first missing stage in pipeline order; look at its logs. |
| `agent4 ran but sent 0/N emails` | Delivery ran and every send failed. | SendGrid (key, account state, quota). The alert itself goes through SendGrid too, see below. |
| `agent4 had X/N failed sends` | Informational. Some sends failed. | `agent4_send_summary.failures` on the run doc. |
| `agent4 failed … StaleNewsletterError` (as `agent4_error`) | agent 4 refused a newsletter older than 24 hours. The error is written on the **stale run's own** doc, not the new one. | Find which stage stalled in the current run (the first missing stage above). |

### Which stage stalled?
Open `pipeline_runs/<run_id>` in the Firestore console (run ids look like `2026-10-05T100004Z`). Fields in order: `scored_papers`, `news_filtered`, `paper_summaries`, `news_summaries`, `newsletter_composed`, `agent4_send_summary`. The first one missing is where it stopped. `{agent}_error` and `{agent}_failed_at` name a caught failure. `agent2b_attempts` means agent 2b recovered after a retry.

### Run doc size
A run doc must stay under Firestore's 1 MiB cap. A write that fails with a size error in agent 3 is the 2026-09-28 failure mode, see the [postmortem](postmortems/2026-09-28-firestore-doc-size.md). There is no size alarm.

### No email arrived
1. Look at the healthcheck service logs for a `[healthcheck]` line (`Report sent`, or a Python traceback).
2. If it logged a send but nothing arrived, SendGrid is the suspect: the health check uses the same SendGrid credential as the newsletter, so a dead credential also silences the alert. This is a known limitation, deliberately not fixed. Check SendGrid directly.
3. Check Cloud Scheduler for the Sunday 1:15 PM (`/?check=draft`) and Monday 7:10 (`/?check=send`) jobs and the `ALERT_EMAIL` setting on the healthcheck service.

## 2. Re-run a stage

Every agent service accepts `POST /` and starts the agent in a background thread, returning `{"status": "started", "agent": …, "run_id": …}` immediately. Watch the logs for completion.

The `run_id` matters. A POST with no usable body generates a **new** run id, which is right for starting a fresh run but wrong for repairing an existing one, because agents read and write `pipeline_runs/<run_id>`. To repair a run, send a Pub/Sub-style envelope carrying the run id:

```powershell
$id  = "<run_id>"
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((@{run_id=$id} | ConvertTo-Json -Compress)))
$body = @{message=@{data=$b64}} | ConvertTo-Json -Compress
$url = gcloud run services describe <SERVICE> --format "value(status.url)"
$tok = gcloud auth print-identity-token
Invoke-RestMethod -Method Post -Uri $url -Headers @{Authorization="Bearer $tok"} -ContentType "application/json" -Body $body
```

Rules of thumb (from the code, not from a test run):
- Services: `agent1a`, `agent1b`, `agent2a`, `agent2b`, `agent3`, `agent4`, `orchestrator`, `healthcheck`.
- To re-run agent 3 or later on an existing run, the earlier stages' outputs must already be on the run doc.
- Running a stage twice is safe. agent 2a and 2b each count once per run, so a repeat never triggers agent 3. agent 3 skips a run that is already composed; its first compose shrinks the article pools on the run doc, so to recompose, re-run agent 2a and 2b first and then call agent 3 with `?force=1`.
- agent 4 keeps a per-recipient record under `pipeline_runs/<run_id>/sent`, so a re-run (within 24 hours) mails only the people not yet reached; the summary's `already_sent` says how many were skipped. To mail everyone again, delete that subcollection. Test sends (`TEST_SEND_TO`) ignore the record.
- agent 4 selects its own newsletter (the newest `newsletter_composed` **release** run, see section 2b) and takes no run id. It refuses anything older than 24 hours, so a re-send is only possible within a day of agent 3 finishing, and a refusal writes an error onto that run's doc. Do not trigger `agent4-test` between runs.
- Test sends: `TEST_SEND_TO` on the service skips the subscriber list.
- A manual run started after 07:30 is bounded only by the 1-hour cap.

## 2b. Release vs debug runs, and previewing a debug run

Each `pipeline_runs` doc has `run_kind`: `release` or `debug` (no field = release, for old runs). The public site preview, agent 4's send and the health check only follow release runs, so a debug run can never replace the published issue or be mailed to subscribers.

- The orchestrator defaults to `debug`. The scheduled job `create-newsletter` must call `<orchestrator-url>/?kind=release`; if it does not, nothing publishes and the health check reports a stale run.
- To try something, start a debug run: call the orchestrator with no `kind` (it defaults to `debug`). The `create-newsletter-debug` job does exactly this, but it is paused and `gcloud scheduler jobs run` refuses paused jobs (`Job.state must be ENABLED for RunJob`). Either resume it, run it, and pause it again right away (its schedule only fires Sundays at noon), or send the same request yourself, which leaves the job paused: `curl -X POST -H "Authorization: Bearer $(gcloud auth print-identity-token)" <orchestrator-url>/` (needs invoker access on the orchestrator; untested). Never add `?kind=release` for this, that is a release.
- Preview a debug run: sign in to the site with an admin account (`ADMIN_EMAILS` on agent-subscriptions) and click **Debug run** in the top bar (or the toggle on the Latest Issue page). It shows the newest composed run of any kind; "Back to published" returns to the public issue.
- To publish a debug run by hand (rare), set `run_kind` to `release` on its doc in the Firestore console.

## 2c. Newsletter archive

`GET /archive` lists, and `GET /archive/{run_id}` serves, composed runs whose `run_kind` is **explicitly**
`release`; the Latest Issue page (`preview.html`) shows them as a date switcher. Runs with no `run_kind` are
*not* archived, even though `/preview` and agent4 treat them as release. Old Monday-morning issues were flagged
once with `venv\Scripts\python tools/backfill_release_flag.py` (dry run), then `--apply`; re-running is a no-op.
To add or hide an issue, set or remove `run_kind` on its `pipeline_runs` doc. Deploy agent-subscriptions, then hosting.

## 3. Roll back

| Change | How |
|---|---|
| agent 1b graph and review loop | `gcloud run services update agent1b --update-env-vars AGENT1B_MODE=single_pass` (no redeploy). Back with `graph` or by removing the variable. The review step is off by default (`REVIEW_MAX_ARTICLES=0`); turn it on with `REVIEW_MAX_ARTICLES=30`. |
| Click tracking | `gcloud run services update agent4 --update-env-vars CLICK_TRACKING=false` restores the previous email exactly. Do the same on `agent4-test`. |
| Any service's code | In the Cloud Run console, send traffic back to the previous revision, or run `gcloud run services update-traffic <SERVICE> --to-revisions <REVISION>=100`. List revisions with `gcloud run revisions list --service <SERVICE>`. |
| A bad image | Rebuild from a good commit with `gcloud builds submit --config cloudbuild.yaml` (all nine images; `cloudbuild-partial.yaml` and `cloudbuild-subscriptions.yaml` build subsets), then `gcloud run services update <SERVICE> --image <IMAGE>`. |

Use `--update-env-vars` and `--update-secrets`, never `--set-*`: the `--set-*` forms replace everything on the service. Building images does not deploy them; each service is updated separately.

After any redeploy, check that `FRONTEND_BASE_URL` is `https://newsletter.lofeodo.com` on both `agent4` and `agent-subscriptions` (see the [2026-09-11 postmortem](postmortems/2026-09-11-frontend-base-url.md)).

## 4. Rotate a key

API keys are mounted from Secret Manager under the same variable names as the code expects, so the code does not change.

1. Create the new key at the provider.
2. Add it as a **new version** of the existing secret (Secret Manager console, or `gcloud secrets versions add <SECRET> --data-file=-` fed from stdin, so the value never lands in shell history or a file).
3. Cloud Run reads `latest` at instance start. Start a new revision so it picks the version up: `gcloud run services update <SERVICE> --update-secrets <VAR>=<SECRET>:latest` for each service that uses it.
4. Check the service logs for a successful call, then disable the old secret version.

Which secret feeds which service is in the service definitions (`gcloud run services describe`), not here, on purpose. SendGrid is the one that also silences the health check if it fails, so confirm a test send works after rotating it.

For the Firebase Admin signing identity (Google Sign-In token exchange), see CLAUDE.md: it needs `roles/iam.serviceAccountTokenCreator` on the service account itself.

## 4b. Sign-up pacing
- Confirmation and link emails are paced per address and per day (`agents/mail_throttle.py`, settings in `docs/configuration.md`). A skipped mail still gets the normal answer on the page; the service logs `mail skipped: <reason>` and, at the daily ceiling, an ERROR line. If a real person says no mail arrived, check those lines first.
- State lives in Firestore collections `mail_throttle` and `mail_budget`; clearing a doc resets that address or day.
- One-time Firestore TTL rules (not yet created) keep these tidy: field `expires_at` on collection groups `subscribers` (never-confirmed sign-ups, 7 days), `mail_throttle` and `mail_budget`.
- The rate limiter counts in each instance's memory, keyed on the last `X-Forwarded-For` entry on Cloud Run; the log line `limiter key: forwarded hops=N` shows what it saw.

## 5. Known gaps
- No alarm on run document size.
- The alert path shares SendGrid with the newsletter.
- The native abort in agent 2b is mitigated, not root-caused.
- agent 4's re-run window is 24 hours.
