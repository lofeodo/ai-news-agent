# Plan A: Topic signup and sending

Part of the [newsletter platform](newsletter-platform.md) (roadmap **A**). Can run in parallel with B, C, D and F.
Branch `feat/topic-signup`, one PR, one commit per step. The worker finishes with `/ship`.

**Goal.** Let people subscribe free to the finance, video games, board games and 3D printing newsletters and get them
every Monday, exactly like the AI newsletter: weekly drafts, signup, per-newsletter unsubscribe, sending and the
weekly health check. Each new newsletter stays hidden until the owner switches it on, so the owner reads real weekly
issues (mailed only to admins) before strangers can sign up.

## Decisions already made

Owner's answers (2026-10-16): launch per topic behind a switch; ready-made newsletters are free; payments are a later
roadmap. Planner's assumptions (the worker may refine them and record changes in the PR):

- **AI readers see no change.** Same AI email (byte-identical), same footer links, same send job, same health check
  verdicts. Every existing test stays green.
- **Launch switch = env var `PUBLIC_TOPICS`** on agent-subscriptions (comma-separated topic ids; `ai` is always
  public). A hidden topic: absent from public pages, signup and archive (404), but admins (`ADMIN_EMAILS`) see it
  with a "hidden" badge and can subscribe to it, which is how the owner gets the pre-launch issues. No redeploy to
  launch: `gcloud run services update agent-subscriptions --update-env-vars PUBLIC_TOPICS=finance,gaming`.
- **Release runs allowed for every topic.** The "non-AI topics are debug-only" rail is lifted: the owner's Scheduler
  jobs decide which topics run weekly, `PUBLIC_TOPICS` decides who can subscribe, and every lookup becomes per topic.
  Anything that follows "the latest issue" without a topic keeps meaning the AI newsletter.
- **Schedule (owner creates the jobs; the plan only documents them).** Sunday drafts are staggered so the AI run keeps
  its noon slot and the 1:15 PM draft check covers everything: finance 10:00, gaming 10:30, board games 11:00,
  3D printing 11:30, AI 12:00. Monday sends: finance 6:40, gaming 6:45, board games 6:50, 3D printing 6:55, AI 7:00
  (unchanged), send check 7:10 (unchanged). A Sunday 10:00 draft is 20h40m old at 6:40 Monday, inside agent4's 24h
  staleness limit.
- **Subscription model: contract C1** in the master plan (`newsletters` list on the subscriber doc, legacy `active`
  kept as the AI flag, `&n=<id>` on email links, no `n` = AI). Code works with and without the backfill of existing
  subscribers.
- **One email per newsletter.** Someone subscribed to three newsletters gets three emails on Monday. Bundling them is
  what a custom newsletter is for (roadmap E).
- **Consent.** Each subscription records when it was made and which consent wording was shown
  (`consent_text_version`), and every signup form names the newsletters being subscribed to. Adding a newsletter
  through the email form needs a confirmation click for that addition (someone could type another person's
  address); through a signed-in account (verified email) or a valid preferences link it is instant.
- **Headcount cap:** `MAX_SUBSCRIBERS` keeps counting people, not subscriptions: a derived boolean `subscribed_any`
  on the subscriber doc, maintained by the helper. Count = `active == True` plus (`active == False` and
  `subscribed_any == True`), two equality queries, no composite index.
- **Health check:** one email as today, with a section per newsletter. Expected newsletters = AI plus every topic that
  had a release run in the last 15 days, so a topic whose weekly job stops is flagged ("no release run this week")
  without any extra configuration.
- **Only AI keeps French/Canada variants and the premium section reorder.** New topics have the single `0_0` variant
  and no per-topic preferences beyond subscribed/unsubscribed.

## Files this roadmap owns (parallel work)

Owns: `agents/subscriptions_model.py` (new), `agents/launch.py` (new), `agents/agent4_send.py`,
`agents/agent_subscriptions.py` (subscription and archive routes), `agents/agent_healthcheck.py`,
`agents/run_kind.py`, `orchestrator.py`, top-bar markup and `nav.js`, `public/newsletter/newsletters.html` (new),
`newsletter.html` (new), `preferences.html`, `unsubscribe.html`, `issues.html`, `index.html` (signup form only).
Does **not** touch: `agents/agent3_compose.py` (B), `agents/topics.py` and `topics/*.yaml` (C; if a helper is
unavoidable, add it as a new function at the end of the file), `agents/widgets/` (C), `public/newsletter/studio*`
(D).

## Steps

Run `venv\Scripts\python -m pytest -q` before every commit.

1. **Subscription model helper.** `agents/subscriptions_model.py`: `subscribed_ids(doc)` (legacy doc with `active`
   and no `newsletters` → `["ai"]`), `is_subscribed`, `add(doc, id, consent_version)` / `remove(doc, id)` returning
   the field updates (keeps `active` in sync with `ai`, maintains `subscribed_any`, `newsletter_meta`).
   *Test:* `tests/test_subscriptions_model.py` (legacy docs, add/remove each way, AI flag sync).

2. **Backfill tool.** `tools/backfill_newsletters_field.py` sets `newsletters` / `subscribed_any` on existing
   subscriber docs (dry run unless `--apply`, prints counts only, never addresses). The owner runs it; the code does
   not depend on it. *Test:* tool test with the fake Firestore.

3. **Launch switch and public list.** `agents/launch.py`: `public_topic_ids()` from `PUBLIC_TOPICS` (unknown ids
   ignored with a log line, `ai` always in), `visible_topic_ids(user)` (admins see all). Public
   `GET /newsletters` → `[{id, name, tagline, accent_color, sections: [{name, icon}], latest_issue: run_id|null}]`
   for visible topics, rate-limited like `/issues`. *Test:* hidden topic absent for anonymous and non-admin, present
   with `hidden: true` for an admin.

4. **Release runs for every topic.** `topics.check_run_request` no longer refuses `kind=release` for valid topics
   (move the rule's replacement into `launch.py` if the function must stay; do not edit `topics.py` beyond deleting
   the refusal). `run_kind.latest_run(..., topic=<id>, release_only=True)` and `pick_release_id` work per topic;
   calls without a topic still mean AI. Raise scan limits where five weekly topics could push the AI run out of the
   window (e.g. 50 → 200). *Test:* extend `test_ai_only_lookups.py` / `test_run_kind.py`: a newer finance *release*
   doc never wins an AI lookup; a finance lookup returns the finance doc.

5. **Overlapping runs are safe.** Five topic runs now overlap on Sunday morning. Check how each pipeline service
   handles two runs at once: the watchdog's `os._exit` ends the whole process, so if two runs can share one
   container, one run's timeout would kill the other. Read the deployed concurrency settings from
   `cloudbuild*.yaml` / docs (not from production). If sharing is possible, either make the watchdog end only its
   own run (preferred if simple, e.g. agents in `_ISOLATED_RETRIES` already run in a child process) or put
   `--concurrency=1` for the pipeline services in the deploy request. Also confirm the stage counters, sent markers
   and `spotlighted_papers` are keyed per run or AI-only. *Done:* finding written in `docs/architecture.md`; a test
   if code changed.

6. **Signup backend.** `POST /subscribe` takes `newsletters: [ids]` (default `["ai"]`, unchanged behaviour for
   today's form); confirmation activates all of them; an already-confirmed address adding a newsletter gets a
   confirmation mail for that addition (`pending_newsletters`, same pacing and response floor as today).
   `POST /auth/subscribe` takes `newsletter` (default `ai`). Hidden topics: 404 unless admin. `send_latest` mails
   the newest *sent* release of that newsletter (`run_kind.latest_run(topic=..., sent_only=True)`), or nothing if
   there is none yet. Headcount cap per the decision above. Confirmation and welcome emails name the newsletter.
   *Test:* extend `test_request_flow.py`, `test_account_routes.py`, `test_public_routes.py`: AI-only flows
   unchanged; finance signup; hidden topic 404; pending addition needs the click.

7. **Unsubscribe and preferences per newsletter.** `GET/POST /unsubscribe?token=&n=` (no `n` = AI): page names the
   newsletter and offers "unsubscribe from everything". `GET/POST /preferences` (token and account versions) return
   and accept the list of subscribed newsletters (removal instant; addition instant because the token or account
   proves the inbox). AI French/Canada prefs unchanged. *Test:* `test_email_link_pages.py` and new cases; an old link
   without `n` still unsubscribes from AI only.

8. **agent4 sends any newsletter.** `main.py` passes `?topic=<id>` to agent4 (default AI; validated like the
   orchestrator). `_load_latest_newsletter(db, topic)` uses that topic's latest release run;
   `_active_subscribers(db, topic)` uses `active == True` for AI and `array_contains` otherwise; footer links get
   `&n=<id>` for non-AI only; `_apply_section_config` stays AI-only; sent markers, click tracking and the send
   summary work per run as today. `TEST_SEND_TO` works per topic. *Test:* fake-Firestore send of a finance run mails
   only finance subscribers with `n=finance` links; existing agent4 tests unchanged.

9. **Health check covers every running newsletter.** Expected newsletters per the decision above. For each: stale
   run, `*_error` fields, missing stages, composition (per the topic's own variants, with the spotlight required
   only for `spotlight: papers`), send summary on the send check. One email; subject stays "all clear" only if every
   newsletter is clear; the AI section reads as today. Drift, judge, usage and click sections stay AI-only.
   *Test:* extend the healthcheck tests: AI-only world unchanged; a failed finance draft flags the email; a topic with
   no release run in 15 days is not expected.

10. **Archive per newsletter.** `GET /issues?topic=` and `/issues/{run_id}` serve explicit release runs of visible
    topics (generalize `run_kind.is_archived_release` per topic; AI unchanged); hidden topics 404 for non-admins.
    *Test:* `test_archive_endpoint.py` / `test_issues_routes.py` new cases.

11. **Newsletters hub page (UI).** `public/newsletter/newsletters.html`: a card per visible newsletter (name,
    tagline, accent colour, its sections with icons, "read the latest issue"), subscribe toggles for signed-in users
    and an email form with one checkbox per newsletter for everyone else. "Newsletters" link in the top bar of every
    page. Use the `frontend-design` skill; keep the site's existing look. *UI check:* desktop, Android, iOS.

12. **Topic landing page (UI).** `public/newsletter/newsletter.html?id=<topic>`: one template page for every topic
    (hero with the newsletter's name, tagline, accent; what's inside; latest issue in the sandboxed iframe; signup),
    filled from `GET /newsletters`. `issues.html?n=<topic>` shows that topic's archive. *UI check:* desktop, Android,
    iOS.

13. **Account and email-link pages (UI).** `preferences.html` gets "Your newsletters" (toggles, AI's French/Canada
    options shown only under AI); `unsubscribe.html` handles `n`. *UI check:* desktop, Android, iOS.

14. **Docs.** CLAUDE.md (topics are now subscribable; `PUBLIC_TOPICS`; release runs for every topic; agent4
    `?topic=`), `docs/architecture.md`, `docs/configuration.md`, `docs/runbook.md` (**topic launch checklist**:
    create the two Scheduler jobs, read the first release draft via the admin view, check the admin received the
    Monday issue, then add the topic to `PUBLIC_TOPICS`), `docs/monitoring.md` (per-newsletter health check),
    `docs/plans/newsletter-platform.md` if a contract changed.

## UI check

Yes: steps 11 to 13. Desktop, Android (Pixel 7) and iOS (iPhone 15) screenshots against a mock or local API before
each of those commits; flag overflow and tap-target problems on phones.

## Deploy needs (agents never deploy; this feeds the deploy request)

Order: **agent4** and **agent4-test** (per-topic sends; AI default unchanged), **healthcheck**, **orchestrator**
(release runs for every topic), **agent-subscriptions** (signup, unsubscribe, archive, `/newsletters`; leave
`PUBLIC_TOPICS` unset at first so nothing new is public), then **hosting**. Pipeline services only if step 5 changed
their code or concurrency.

Owner actions after the deploy, per topic, one at a time:
1. Run the backfill tool (`--apply`) once.
2. Check the SendGrid plan's daily and monthly quota covers the extra emails.
3. Subscribe yourself to the hidden topic from the hub page (admin).
4. Create the two Scheduler jobs (Sunday draft `orchestrator POST /?topic=<id>&kind=release`, Monday send
   `agent4 POST /?topic=<id>`) at the times above; the exact commands go in the deploy request.
5. After a good Sunday draft and Monday issue: add the topic to `PUBLIC_TOPICS`.

Timing: deploy Monday after the 7:10 AM send check or any day to Thursday; never Sunday before noon through Monday
7:10 AM. Rollback: previous image per service; un-launch a topic by removing it from `PUBLIC_TOPICS`; stop a topic by
pausing its two Scheduler jobs.

## Out of scope / follow-ups

- Bundling several newsletters into one email (that is a custom newsletter, roadmap E).
- Per-topic French/Canada variants and per-topic premium section reorder.
- Per-topic drift, judge, usage and click sections in the health check.
- A platform home page replacing the AI landing page; a platform brand.
- Per-topic share pages and social cards.
