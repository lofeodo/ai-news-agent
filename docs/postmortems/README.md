# Postmortems

Blameless write-ups of real incidents in the weekly pipeline. Facts come from the repo history and the owner's recollection; anything unknown is stated as unknown. See [TEMPLATE.md](TEMPLATE.md) for the format and [../runbook.md](../runbook.md) for how to diagnose and recover.

| Date | Incident | Root cause confidence |
|---|---|---|
| 2026-09-07 | [Stale newsletter re-sent](2026-09-07-stale-newsletter.md) | Trigger (native crash in agent 2b) unconfirmed; the failure chain around it confirmed |
| 2026-09-28 | [Run document over Firestore's 1 MiB cap](2026-09-28-firestore-doc-size.md) | Confirmed |
| 2026-09-11 | [Dead preferences link in the newsletter footer](2026-09-11-frontend-base-url.md) | Confirmed cause; start date unknown |
