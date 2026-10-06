# 2026-09-28: the run document exceeded Firestore's 1 MiB limit

Status: draft
Sources: commit messages `53f832b`, `894d124`, `d4869b2`, `bec6e2c`; CLAUDE.md; the code.

## Summary
On Monday 2026-09-28 agent 3 failed writing the composed newsletter because the `pipeline_runs/{run_id}` document had grown to about 1.2 MB, over Firestore's 1 MiB document cap. The same day agent 2b hit its native SIGABRT crash again. Both were fixed the same day.

## Impact
- The 2026-09-28 pipeline run did not complete on its own. OWNER: add whether a newsletter was sent that week, and if so when and how (manual rerun?).
- No other run is known to be affected, but earlier run sizes were not checked. The document grows with the number of articles, not with code changes.

## Timeline
- 2026-09-28 (Monday): agent 2b crashes with `free(): invalid pointer`, SIGABRT, about 3 seconds into summarizing, the second time (first: 2026-09-07). Agent 3 fails writing the variants with the document at about 1.2 MB (`894d124`).
- 2026-09-28: fixes committed and merged the same day (PR #46 for agent 2b, PR #49 for document size).

## Root cause
**Confirmed for the size failure.** Everything the pipeline stores on one run document accumulated: the full article pools (about 850 KB at about 500 articles) plus the four rendered newsletter variants (about 420 KB) exceeded 1 MiB. The article pools were also stored twice in `news_filtered` (grouped by category and as a flat `articles` list, about 210 KB at 500 articles, `53f832b`).

**Unconfirmed for the agent 2b crash**; see the [2026-09-07 postmortem](2026-09-07-stale-newsletter.md).

## Detection
By failure: agent 3's write was rejected. Agent 3 records `agent3_error` on uncaught failures, which the health check reports, but the write that records the error goes to the same oversized document, so whether it landed is not established here. OWNER: add whether it was the health check email or logs that surfaced it.

## Fixes
- `53f832b` agent 1b stops storing the duplicate flat `articles` list; agent 2b builds its list from `by_category`.
- `894d124` agent 3, in the same write that saves the variants, shrinks `news_summaries` to just the shipped articles and `news_filtered` to per-category counts. Both fields stay non-empty because the health check tests them for presence.
- `d4869b2` and `bec6e2c`: agent 2b serializes `parse()`, skips image fetching, and runs in a crash-isolated child process with retries and an idempotent counter increment.

Regression tests: see the Step 8 entry in the roadmap.

## Lessons
- A single document that every stage appends to has a hard ceiling that depends on input volume, not on code. Size should be watched, not just fixed once.
- Fields that a monitor checks for presence constrain how a fix can shrink them.
- A crash with no trace in the run document made the 09-07 incident hard to see; the retry and error-recording work closes that gap for agent 2b only.

## Follow-ups
- The run document has no size alarm. If article volume grows, the same limit will be reached again. Unassigned.
- The cause of the native abort is still unconfirmed.
