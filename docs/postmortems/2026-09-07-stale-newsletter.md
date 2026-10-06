# 2026-09-07: the previous newsletter was sent again

Status: draft
Sources: commit messages `83627a5`, `374e19e`, `24a4511`, `d137c7b`, `d4869b2`, `bec6e2c`; the code; the owner's recollection (marked).

## Summary
On Monday 2026-09-07 agent 2b crashed with a native abort a few seconds into summarizing. The pipeline never reached agent 3, so no new newsletter was composed. At 7 AM agent 4 picked the most recent composed newsletter, which was the 2026-08-24 issue, and sent it to subscribers with a clean-looking send summary. The weekly health check, which should have reported the stalled run, had itself been failing silently since 2026-08-17.

## Impact
- All active subscribers received the 2026-08-24 issue again instead of a new one (owner). No reader complaints were received (owner).
- The number of subscribers is not recorded in this document. OWNER: add the count if you want it public.
- No newsletter was sent for the 2026-09-07 week.
- The health check heartbeat was missing for three Mondays (2026-08-17 to 2026-09-07), so there was no alert at all that morning.

## Timeline
- 2026-08-10: the failure-recording fields (`{agent}_error`) and the health check were added (`6c27167`, `25cbce4`).
- 2026-08-17: the health check starts raising `TypeError: can't subtract offset-naive and offset-aware datetimes` before sending its email, every Monday, so the heartbeat silently stops (`374e19e`).
- 2026-09-07, before 7 AM: agent 2b dies with `munmap_chunk(): invalid pointer`, SIGABRT (exit 134), about 5 seconds into summarizing (`83627a5`). Python's `try/except` cannot catch it, so no error is written to the run document. The `agent2_completions` counter never reaches 2, `content-summarized` is never published, agent 3 never runs.
- 2026-09-07, 7 AM: agent 4 queries for the newest run with `newsletter_composed == True`, finds the 2026-08-24 run (440 hours old, per `d137c7b`) and sends it.
- 2026-09-07: the owner notices the duplicate issue (owner). OWNER: add the time and how soon after the send.
- 2026-09-11: fixes merged (see Fixes).
- 2026-09-28: the same native crash happens again (see the [2026-09-28 postmortem](2026-09-28-firestore-doc-size.md)).

## Root cause
Three independent faults lined up.

1. **The agent 2b crash. Confidence: trigger unconfirmed.** A native heap-corruption abort inside agent 2b. The prime suspect is 20 threads running newspaper3k's `Article.parse()` at once (lxml/libxml2, and Pillow when image fetching is on). It was not reproduced, so the change in `d4869b2` is a mitigation, not a proven fix. It recurred on 2026-09-28, before any crash mitigation existed. No recurrence has been observed since the 2026-09-28 fixes (owner, 2026-10-06), but only a few runs have passed.
2. **agent 4 had no freshness check. Confidence: confirmed.** `_load_latest_newsletter()` took the newest `newsletter_composed` run with no age limit, so any stall before agent 3 silently resolved to an older, successful run.
3. **The health check was broken. Confidence: confirmed.** agent 1a overwrote the orchestrator's timezone-aware `started_at` with a naive `datetime.now().isoformat()`. The health check subtracted it from an aware "now" and raised before sending anything.

## Detection
Found by the owner noticing the repeated content, not by any monitor. The two systems meant to catch this were blind: agent 4 reported success, and the health check was crashing. A native abort leaves no trace in Firestore, and the stalled run left nothing that looked like an error.

## Fixes
- `83627a5` hard runtime watchdog in `main.py`: bounds hangs (1 hour, or 07:30 Toronto time) and records `{agent}_error`. It cannot help a process that has already aborted, which is why this incident was not prevented by it.
- `374e19e` health check treats a naive `started_at` as UTC (`config.parse_started_at`); `24a4511` agent 1a writes an aware UTC `started_at`.
- `d137c7b` agent 4 refuses a newsletter older than `MAX_NEWSLETTER_AGE_HOURS` (24) by raising `StaleNewsletterError`, recorded on the stale run's own document so the health check's latest-run lookup still sees it.
- `d4869b2` agent 2b serializes `parse()` behind a lock and turns off image fetching; `bec6e2c` runs agent 2b in a child process with up to 5 retries, with an idempotent counter increment. These came after the 2026-09-28 recurrence.

Regression tests: see the Step 8 entry in the roadmap for which of these now have one. Anything not listed there is untested.

## Lessons
- A monitor that can crash silently is not a monitor. The health check now converts its own errors into a "problem detected" email.
- Fallback behavior that returns the last good value hides outages. The freshness guard turns a quiet wrong answer into a loud failure.
- Python-level error handling cannot see a native abort. Process isolation and an external check on missing stages are the only places it can be caught.

## Follow-ups
- Root cause of the native abort is still unconfirmed. Unassigned.
- OWNER: fill in the subscriber count and detection time above, if wanted.
