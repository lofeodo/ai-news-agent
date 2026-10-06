# 2026-09-11: the preferences link in the newsletter footer was dead

Status: draft
Sources: CLAUDE.md (`FRONTEND_BASE_URL` row), commit `f44579e`, the code.

## Summary
The deployed `agent4` service had `FRONTEND_BASE_URL` set to a URL with the wrong host and a path that does not exist. Every `{{PREFERENCES_URL}}` substituted into a sent newsletter footer was therefore a dead link until the value was corrected on 2026-09-11.

## Impact
Subscribers who clicked "manage preferences" in a newsletter footer reached a dead page. The unsubscribe link was not affected (it is built from `SERVICE_BASE_URL`). The number of affected sends and clicks is unknown: the start date was not recorded and no click data existed then.

## Timeline
- Start: unknown (owner does not know when the wrong value was set).
- 2026-09-11: the value on `agent4` was corrected, and the guidance was recorded in CLAUDE.md (`f44579e`).

## Root cause
**Confirmed.** A configuration error, not a code bug: the value was `https://lofeodo.com/newsletter` (wrong host; `/newsletter` is not a real path, because Firebase Hosting serves `public/newsletter/*` at the site root). The correct value is `https://newsletter.lofeodo.com`. `agent-subscriptions` had the correct value the whole time, so the two services disagreed.

## Detection
OWNER: add how it was found. Nothing automated checks link targets in a sent newsletter.

## Fixes
Configuration only: corrected the environment variable on `agent4`. No code change, so there is no code regression to test; a test can only show that footer links are built from the variable, not that the deployed value is right.

## Lessons
- The same setting existing on two services can drift. After any redeploy, check both.
- A dead link in a sent email is invisible to the pipeline's own checks.

## Follow-ups
- A check that fetches the footer links of the composed newsletter before sending, or a deploy checklist that compares the variable across services. Unassigned.
