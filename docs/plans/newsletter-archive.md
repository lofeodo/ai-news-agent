# Plan: Newsletter archive

Goal: visitors can read any past release issue, not just the latest. The archive is folded into the existing
Latest Issue page (`preview.html`) as a date switcher, so there is one page, not two.

Each step below is its own PR, stacked on the previous one. A step is checked off in the PR that completes it.

## Findings (Firestore `pipeline_runs`, read 2026-10-15)

- Only 2026-10-05 and 2026-10-11 carry `run_kind: "release"`; 2026-10-07/08/09 are `debug`. Everything older has no
  `run_kind`, which `run_kind.is_release()` treats as release.
- Issues were generated Monday ~10:00 UTC (06:00 Toronto) until the move to Sunday noon. Composed Monday-morning
  runs without a flag: 06-22, 06-29, 07-13, 07-20, 07-27, 08-03, 08-17, 08-24, 09-21.
- 09-28 is special: the 10:00 run failed (agent3 400, the 1 MiB doc cap), and two recovery runs followed
  (`13:21Z`, `18:37Z`). Only one send happened (agent4 summary at 20:45Z), so `2026-09-28T183736Z` is the issue
  that shipped. Include it; skip `132134Z`.
- No composed issue exists for 06-15, 07-06, 08-10 (SendGrid outage), 09-07 (agent2b crash), 09-14.
- Non-Monday composed runs (07-05, 08-12, 09-11, 09-30) are recovery or test runs. They stay unflagged.
- The existing `(newsletter_composed, started_at)` index covers the archive query; no new index is needed.
  Filtering to `run_kind == "release"` happens in code on a small `select()` scan.

## Decisions

- **The archive only lists runs with an explicit `run_kind == "release"`.** The "no field means release"
  default stays for `/preview`, agent4 and the health check (unchanged), but the archive must not surface
  recovery and test runs just because they predate the field. The backfill is what puts an old issue in the archive.
- **Backfill is a script, dry-run by default**, writing only `run_kind: "release"` (plus `run_kind_backfilled_at`)
  to a fixed list of run ids it prints first. It never touches other fields and never sets `debug`.
- **New public routes, `/preview` untouched.** `GET /archive` (list) and `GET /archive/{run_id}` (HTML, same
  rendering and CSP as `/preview`). A run id that is not an explicit release returns 404, so a debug run can
  never be fetched by id.
- **Fold into `preview.html`.** Keep the URL and the `ALLOWED_RETURN_PAGES` entry so existing links work. Add a
  prev / next / dropdown switcher above the iframe; `?issue=<run_id>` is deep-linkable. Nav label becomes "Issues".
- The admin Debug run toggle keeps working and takes precedence while on.

## Steps

- [x] **1. Plan** (this PR): `docs/plans/newsletter-archive.md`.
- [x] **2. Backfill script**: `tools/backfill_release_flag.py` + tests. Dry run (2026-10-15) lists 10 runs: the
      nine Monday-morning ids above plus `2026-09-28T183736Z`. **Owner action:** run
      `venv\Scripts\python tools\backfill_release_flag.py --apply` (writes were blocked for the agent).
      Safe to run before or after any other step; re-running is a no-op.
- [x] **3. Backend**: `run_kind.list_releases()`, `GET /archive`, `GET /archive/{run_id}`, rate limits, tests.
- [ ] **4. Frontend**: switcher in `preview.html` (date dropdown, prev/next, keyboard arrows, deep link, empty
      and error states); nav label; screenshots on desktop, Android (Pixel 7) and iOS (iPhone 15).
- [ ] **5. Docs**: CLAUDE.md, `docs/architecture.md`, `docs/repository-structure.md`, `docs/runbook.md` (deploy
      agent-subscriptions, then hosting).

Deploy is not part of any step; it is the owner's `gcloud run deploy` of agent-subscriptions followed by a hosting
deploy, after steps 3 and 4 are merged.

## Out of scope

- Pre-June issues and the gap weeks above (no composed newsletter exists).
- Per-subscriber variants: the archive shows the Canada-inclusive variant like `/preview` does.
- Search, RSS, and static HTML export.
