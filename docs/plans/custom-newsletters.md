# Plan E: Custom newsletters (API, preview and sending)

Part of the [newsletter platform](newsletter-platform.md) (roadmap **E**). **Starts after roadmaps A and B have
merged.** Uses C (widgets), D (studio UI) and F (custom pools) when they have merged; each is optional, and the
steps say what to do when one has not. Branch `feat/custom-newsletters`, one PR, one commit per step. The worker
finishes with `/ship`.

**Goal.** Make the studio real: premium members save their own newsletters, see a live preview built from this
week's actual content, and receive their custom newsletter every Monday morning. This is the first time the platform
sends an email assembled per reader, so it must be cheap (no Claude call per reader), safe (never send a stale or
broken issue) and observable in the weekly health check.

## Decisions already made

From the master plan: the studio is premium and invite-only (`PREMIUM_EMAILS`) until payments; contracts C1 to C7;
cost scales with content. Planner's assumptions (the worker may refine them and record changes in the PR):

- **Storage:** `custom_newsletters/{id}` (`id` per C1) =
  `{owner_uid, definition (C3), subscribed, created_at, updated_at, last_sent_week, last_send_status}`. Up to 3 per
  member (`CUSTOM_NEWSLETTERS_MAX`). Only the owner can read, change or delete one; any other id answers 404.
- **Recipient:** the owner's verified account email at send time (from Firebase Auth via the Admin SDK, falling back to
  `users/{uid}.email`), never an address stored in the definition. Unsubscribe link: a token link with
  `n=<custom id>` that sets `subscribed: false`; reuse the subscriber's existing token when the owner is a subscriber,
  otherwise create a subscriber doc with no active newsletters to hold the token (no new token system).
- **Premium check** on every write, preview and send: tier is `premium` (today: `PREMIUM_EMAILS`). A member who stops
  being premium keeps their saved newsletters but they are not sent (`last_send_status: "not premium"`).
- **Content for a send:** for each topic the definition uses, the release edition for this send week
  (`editions.for_week(db, topic, week_of)`, roadmap B); for each custom section, the C5 pool for this week (roadmap F;
  absent = section dropped with a one-line note in the email); personal widgets through roadmap C's
  `get_or_produce_personal`. A section whose edition is missing is dropped with a note; if no content block is left,
  the newsletter is not sent that week and the status says why. Never fall back to an older week's edition.
- **Intro:** roadmap B's templated intro (no Claude call). A cached Claude intro is a follow-up.
- **Send job:** agent4 `POST /?newsletter=custom`, a new Scheduler job Monday **6:30 AM** Toronto, before the
  ready-made sends (6:40 to 7:00) and the 7:10 send check. One invocation loops over subscribed custom newsletters;
  renders are cached per definition hash within the run (identical definitions render once); sent markers at
  `custom_sends/{week}/sent/{newsletter_id}` so a re-run skips what was sent; summary at `custom_sends/{week}`
  (`total`, `sent`, `failed`, `skipped_*` counts, short hash refs, no addresses). Click tracking stays off for custom
  newsletters in v1.
- **Preview:** `POST /auth/newsletters/preview` renders from the **latest** release edition of each used topic and the
  latest pool of each custom section (or a placeholder block "Your SpaceX section starts with your first issue"),
  personal widgets with `sample_data`, and returns C6's `{html, size_bytes, warnings}`. Rate limit 20/min per user;
  results cached 10 minutes per definition hash. Same `PREVIEW_CSP` and sandboxed `srcdoc` as the archive.
- **Catalog:** `GET /catalog` lists visible topics (roadmap A's `launch.visible_topic_ids`, so hidden topics only for
  admins) with their sections, and widgets from roadmap C's registry if it merged, otherwise just `paper_spotlight`.
- **The old Customize page stays** (AI section reorder) until a follow-up retires it.

## Files this roadmap owns

Owns: `agents/custom_newsletters.py` (new: storage, ownership, limits), `agents/studio_routes.py` (new router: C6
routes, mounted for agent-subscriptions in `main.py`), `agents/custom_send.py` (new: the custom send loop agent4
calls), `agents/custom_send_report.py` (new). Edits: `agents/agent4_send.py` (dispatch `newsletter=custom` to
`custom_send`), `main.py` (pass `newsletter` to agent4, mount the router), `agents/agent_healthcheck.py` (one call),
`public/newsletter/studio/api.js` (real API by default), the top bar on every page and `nav.js` (Studio link for
premium members), `docs/`.

## Steps

Run `venv\Scripts\python -m pytest -q` before every commit.

0. **Check the ground.** `git fetch`; confirm A and B are on `main` (stop and report if not). Note in the PR which of
   C, D, F have merged and which optional steps below therefore apply. Re-read the master plan's contracts as merged.

1. **Storage and ownership.** `agents/custom_newsletters.py`: create (new id), update (owner only, definition
   validated with B's `newsletter_def.parse`, widget ids from C's registry if present), delete, list for a user,
   limit, `subscribed` flag. *Test:* `tests/test_custom_newsletters.py` (limits, ownership, invalid definition errors).

2. **Catalog route.** `GET /catalog` per the decisions. *Test:* hidden topic only for admins; widgets with and without
   C's registry (monkeypatched import).

3. **CRUD and subscribe routes.** C6 `GET/POST /auth/newsletters`, `DELETE /auth/newsletters/{id}`,
   `POST /auth/newsletters/{id}/subscribe|unsubscribe`; premium enforced; 400 carries the validator's error list.
   *Test:* non-premium 403, other user's id 404, unverified email 403, round trip.

4. **Preview route.** Per the decisions, with the rate limit and cache. *Test:* fixture editions in the fake
   Firestore; missing edition gives a warning not an error; size and warnings returned; CSP header or meta present.

5. **Studio on the real API.** If D merged: `studio/api.js` uses the real routes by default (mock only with
   `?mock=1`); add the "Studio" top-bar link, shown to premium members (and admins) only; `tests/test_studio_fixtures.py`
   additionally validates the mock fixtures with B's `newsletter_def.parse`. If D has not merged: skip, note it in the
   PR; D will pick up the real API when it lands. *UI check:* full studio flow against a local API at desktop,
   Android, iOS.

6. **Custom send loop.** `agents/custom_send.py`: `send_custom(db, week_of)` per the decisions (content gathering,
   premium check, per-definition render cache, personalization with `n=<id>` unsubscribe links, sent markers,
   per-newsletter status, summary doc). agent4 dispatches `?newsletter=custom` to it; `TEST_SEND_TO` sends every
   subscribed custom newsletter to that address only (no markers), for the owner's test service. *Test:* fake
   Firestore + fake SendGrid: two members, one identical definition rendered once; missing edition drops a section;
   no content left → not sent; non-premium skipped; re-run skips sent ones.

7. **Unsubscribe link for custom ids.** `GET/POST /unsubscribe?token=&n=c_...` turns `subscribed` off for that custom
   newsletter only (roadmap A's page shows its name). *Test:* only the owner's token can do it; wrong token is the
   usual invalid-link page.

8. **Personal widgets** (only if C merged with a personal widget): preview uses `sample_data`, sends use
   `get_or_produce_personal` with its cache; a failed personal widget is dropped from that email only. *Test:* two
   members with the same config produce once.

9. **Custom pools** (only if F merged): send and preview read `custom_pools/{hash}__{week}`; F's topic collector
   already reads `custom_newsletters`. *Test:* pool present / absent.

10. **Health check section.** `agents/custom_send_report.py`: on the send check, "Custom newsletters: N sent, M
    failed, K skipped (reasons)" from `custom_sends/{week}`; a send job that did not run while subscribed custom
    newsletters exist is flagged as a problem; failures above the existing `SEND_FAILURE_ALERT_RATIO` are flagged.
    One call in the health check. *Test:* formatting and the two flags.

11. **Docs.** CLAUDE.md (custom newsletters, `?newsletter=custom`, collections), `docs/architecture.md`,
    `docs/configuration.md`, `docs/monitoring.md`, `docs/runbook.md` (re-run a custom send; invite a member by adding
    their email to `PREMIUM_EMAILS`), master plan if a contract changed.

## UI check

Yes, step 5 (studio on the real API, including the Studio link in the top bar): desktop, Android (Pixel 7), iOS
(iPhone 15). Also open one custom email produced by the step 6 test fixture at desktop and phone widths.

## Deploy needs (agents never deploy; this feeds the deploy request)

Order: **agent4** and **agent4-test** (custom send loop), **healthcheck**, **agent-subscriptions** (studio API,
unsubscribe for custom ids), then **hosting** (studio on the real API, Studio link). Owner actions: create the
Scheduler job Monday 6:30 AM America/Toronto calling agent4 with `?newsletter=custom`; check the SendGrid quota;
invite testers by adding them to `PREMIUM_EMAILS` on agent-subscriptions and agent4. If F is deployed, its Saturday
job feeds custom sections. Timing: Monday after 7:10 AM to Thursday, so the first custom send is a full week later and
the owner can build and preview their own custom newsletter first; consider pausing the 6:30 job for the first week
and running agent4-test by hand on Monday morning instead. Rollback: pause the 6:30 job (stops all custom sends);
previous image per service.

## Out of scope / follow-ups

- Payments, plans, upgrade flow (roadmap G, written after invited members have used the studio).
- A cached Claude intro per custom newsletter; click tracking for custom newsletters.
- Retiring the old Customize page and moving its saved configs into custom newsletters.
- Sharing, duplicating or publishing a custom newsletter; sending to other addresses.
- Send day and time chosen by the member.
