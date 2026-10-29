# Plan: Subscribe with Google, and show subscription state

Branch `feat/google-subscribe-flow`, one PR, one commit per step. The worker finishes with `/ship`.

## Goal

People who click the Google button on the homepage expect to be subscribed, but today it only signs them in, the
Subscribe button is buried on the preferences page, and no page says whether they are subscribed. After this work the
homepage button subscribes in one click (with a consent line and a recorded consent version), every signed-in page
shows "Subscribed" or "Not subscribed + Subscribe", and plain sign-in from the login page stays a manage-only action.

## Decisions already made

Agreed with the owner in chat (2026-10-29):

- **Signing in never subscribes by itself** (roadmap A rule, kept). The *intent* is carried by which button was
  clicked: the homepage subscribe box's Google button starts sign-in with a dedicated return page, `welcome.html`,
  and that page performs the subscription. The login page's "Continue with Google" returns to the page the reader
  came from and subscribes nothing.
- **The return page is the only signal** that survives the Google round trip (same rule as the existing sign-in
  design: a URL parameter, never browser storage). `welcome.html` is added to the return-page allowlist on both the
  backend and `auth.js`. The three box options (send the latest issue, French, Canada) ride along best-effort in
  `localStorage` under one key written just before navigation and deleted on read; if they are missing the page
  subscribes with "send latest" off and the account defaults, and offers a "Send me last week's issue" button instead.
  Nothing on the critical path depends on storage.
- **Consent.** The subscribe box shows one line under the Google button: "One email a week. Unsubscribe any time."
  The click is the express consent; `welcome.html` posts the same `consent_text_version` the email form uses
  (`GET /newsletters` returns the wording and version), so the subscription record matches an email signup.
- **Welcome page states:** subscribing (spinner) → subscribed ("You're subscribed as *email*. Next issue Monday
  morning." plus "Last week's issue is on its way" when sent; the primary button is "Read the latest issue", since the
  account route cannot send last week's issue after the fact) →
  already subscribed (same card, "You were already subscribed") → failed (error line and a full-width Subscribe
  button that retries) → signed out (a "Subscribe with Google" button that starts sign-in with
  `return_to=welcome.html`). The page is linked from every "Subscribe" affordance for signed-in readers.
- **Status everywhere.** `/auth/me` already returns `subscribed`. `nav.js` caches it with the tier and shows a small
  "Subscribed" pill (or a "Subscribe" link to `welcome.html`) in the top bar account area, and dispatches a
  `lsm:me` event so the homepage can render the same state in its signed-in block without a second request.
- **Homepage signed-in block:** status line first ("Subscribed as *email*" / "Not subscribed"), then the primary
  button: "Read the latest issue" when subscribed, "Subscribe" (to `welcome.html`) when not, then the Preferences and
  Customize links.
- **Preferences page:** when the account is not subscribed, the existing not-subscribed block becomes a bordered
  card at the top of the page with the Subscribe button as its primary action; the logic is unchanged.
- **Backend change is one line** (`welcome.html` in `ALLOWED_RETURN_PAGES`); `/auth/subscribe` already takes
  `send_latest` and `consent_text_version`, and the French/Canada options are saved through `/auth/preferences`
  after the subscription succeeds.
- Custom sections, other topics' signup pages (`newsletter.html`, `newsletters.html`) and payments are out of scope.

## Steps

Run `venv\Scripts\python -m pytest -q` before every commit.

1. **Backend allowlist.** Add `welcome.html` to `ALLOWED_RETURN_PAGES` in `agents/agent_subscriptions.py` and to
   `ALLOWED_RETURN_PAGES` in `public/newsletter/auth.js`. *Done when* a test in `tests/test_subscription_tokens.py`
   (or a new `tests/test_return_pages.py`) asserts the two lists are identical and contain `welcome.html`.
2. **Welcome page.** New `public/newsletter/welcome.html` (top bar like the other pages, `nav.js`, `auth.js`): on
   auth state → signed out: Google button to `/auth/google/login?return_to=welcome.html`; signed in: read and delete
   the options key, fetch the consent version from `GET /newsletters`, `POST /auth/subscribe`, then
   `POST /auth/preferences` with French/Canada when the options were present, render the states above. Add
   `welcome.html` to `nav.js`'s active-link logic only if needed. *Test:* `tests/test_site_pages.py` (new) checks
   the page exists, links `nav.js`/`auth.js`, and contains the four state containers; manual check with the local
   server (`?mock` is not needed: states can be forced from the console).
3. **Homepage subscribe box.** Google button text "Subscribe with Google", `href` built with
   `return_to=welcome.html`, consent line under it; on click, write the options key from the box's checkboxes
   (`send_latest`, `include_french`, `include_canada`). The email form is unchanged. *Test:* extend
   `tests/test_site_pages.py` to assert the homepage Google link targets `welcome.html` and the consent line exists.
4. **Status in the top bar and homepage.** `nav.js`: cache `subscribed`, render pill/link in `#topbar-auth`
   (markup added to every page's top bar next to the tier badge; CSS in `topbar.css`), dispatch `lsm:me`.
   `index.html`: signed-in block renders the status line and swaps the primary button. *Test:* grep-level test that
   every page's top bar has the new element; visual check.
5. **Preferences page card.** Wrap the not-subscribed block in a card style at the top of the page; copy says what
   the newsletter is and the button is primary. *Test:* existing page logic untouched; visual check.
6. **Docs and UI checks.** Update `docs/architecture.md` (sign-in vs subscribe, the welcome page) and the
   CLAUDE.md subscriber paragraph's "Signing in never subscribes by itself" sentence to mention the welcome page.
   Three-viewport screenshots of the homepage (signed out, signed in subscribed, signed in not subscribed), the
   welcome page states and the preferences card.

## UI check

Yes: desktop, Android and iOS screenshots of the homepage, welcome page and preferences page before the last commit.
Signed-in states can be forced locally by toggling the `is-signed-in` body class and dispatching `lsm:me`.

## Deploy needs

1. `agent-subscriptions` (the allowlist line) **first**: a `welcome.html` return target is rejected and falls back to
   the homepage until it is deployed, which would make the new button sign in without subscribing.
2. Firebase Hosting second (new page, homepage, top bar, preferences).

Any weekday is fine; nothing touches the pipeline or the Monday send. Agents never deploy; the final report carries
the deploy request.

## Out of scope / follow-ups

- Subscribe-with-Google on `newsletter.html` / `newsletters.html` for other topics (they keep their account toggle).
- Retiring the "Continue with Google" button on the login page in favour of the welcome page.
- A server-side intent (state doc) instead of the return page, if the allowlist approach ever needs options that
  cannot ride in storage.
