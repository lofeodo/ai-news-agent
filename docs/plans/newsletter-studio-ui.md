# Plan D: Newsletter studio (UI)

Part of the [newsletter platform](newsletter-platform.md) (roadmap **D**). Fully parallel: it builds against a mock of
the studio API (contract C6), so it needs no other roadmap. Roadmap E later switches it to the real API. Branch
`feat/studio-ui`, one PR, one commit per step. The worker finishes with `/ship`.

**Goal.** Build the premium studio: a page where a member builds their own newsletter by picking sections from any
topic, adding widgets and custom sections, ordering them, naming it and choosing its colour, while a live preview
shows exactly the email they will receive, on desktop and on a phone. It must feel polished and fast, and work as
well on a phone as on a laptop.

## Decisions already made

From the master plan: the studio is premium (invite-only until payments); definitions follow contract C3; the API
follows C6. Planner's assumptions (the worker may refine them and record changes in the PR):

- **Mock first, real later.** The page talks to the C6 routes through one small client module. A mock implementation
  of the same module (fixture catalog, saved newsletters in `localStorage`, a client-side preview assembled from
  fixture email fragments, clearly labelled "sample content") is used when the page is opened with `?mock=1` or when
  `GET /catalog` answers 404. Roadmap E only has to make the real routes exist; it does not rewrite the page.
- **Not linked anywhere yet.** The page ships at `/studio.html` with `noindex`, reachable by URL only. The top-bar
  "Studio" link is added by roadmap E (roadmap A owns the top bar). The page carries its own copy of the existing
  top-bar markup.
- **Gating:** signed out → a short pitch and "Sign in"; signed in, free → the upgrade gate (same style as
  `sections.html`) over a read-only demo using the mock; premium → full studio. The page never decides the tier
  itself: it reads `tier` from `/auth/me` (as other pages do); the server enforces it.
- **Layout.** Desktop (≥ 1100 px): three columns, *Library* (topics → sections, widgets, custom section) | *Your
  newsletter* (ordered blocks) | *Preview* (email in a frame, Desktop/Phone width toggle, size meter). Tablet: two
  columns, preview as a toggleable panel. Phone: a bottom tab bar *Build* / *Preview*; the library opens as a sheet
  from an "Add" button. Same visual language as the rest of the site (fonts, colours, `style.css`, `topbar.css`),
  with the newsletter's chosen accent colour echoed in the preview frame.
- **Editing.** Add from the library (tap or drag), reorder by drag (pointer events, so touch works), by up/down
  buttons and by keyboard (focus a block, Alt+↑/↓), remove, set "items" (1 to 5) per section, set widget config
  fields, rename, pick an accent from preset swatches or a hex field. Limits from C3 are enforced in the UI with a
  plain message ("Up to 8 sections"). Undo for the last removal (a toast with Undo).
- **Preview.** Debounced (600 ms after the last change) `POST /auth/newsletters/preview`; shown in a sandboxed iframe
  via `srcdoc` (no `allow-scripts`, same policy as the archive). Loading shimmer, last good preview kept on error,
  `warnings` from the API listed under the frame, size meter turning amber above 90 KB.
- **Custom sections** use the existing, real `POST /auth/sections/refine` (Haiku suggests a precise topic) with the
  same flow as `sections.html` (type → refine → use / edit / add as typed); in mock mode refine is simulated.
- **Saving.** Explicit "Save" plus an unsaved-changes guard on leave; "My newsletters" switcher (up to 3); "Send it
  to me every Monday" toggle (subscribe); delete with confirmation.
- **Accessibility:** every control reachable by keyboard, visible focus, labels on icon buttons, live region for
  save/preview status, `prefers-reduced-motion` respected, contrast AA, tap targets ≥ 44 px.
- **No build step, no framework:** plain ES modules like the existing pages (the site has no bundler and no package
  manager). Firebase SDK stays at 10.14.1 via the existing `auth.js`.

## Files this roadmap owns (parallel work)

Owns: `public/newsletter/studio.html`, `public/newsletter/studio/` (new: `studio.css`, `app.js`, `api.js`,
`mock-api.js`, `model.js`, `dnd.js`, `fixtures/catalog.json`, `fixtures/fragments/*.html`),
`tests/test_studio_fixtures.py`. Does **not** touch: the top bar on other pages, `nav.js`, `auth.js` (import only),
`sections.html`, any Python under `agents/`.

## Steps

Run `venv\Scripts\python -m pytest -q` before every commit (the suite must stay green even though most of this
roadmap is front end).

1. **Fixtures and their test.** `studio/fixtures/catalog.json` in the exact C6 `GET /catalog` shape, built from the
   real `topics/*.yaml` (names, taglines, accents, sections, icons) plus the planned widget catalog from
   [`widgets.md`](widgets.md) (ids, names, descriptions, scope, premium, config fields); email fragments per section
   and widget (taken from real composed drafts' HTML structure, shortened). `tests/test_studio_fixtures.py` checks the
   catalog against the C6 shape and that every topic/section in it exists in `topics/` (so the mock cannot drift from
   the real profiles). *Done:* test green.

2. **Model.** `studio/model.js`: the definition state and pure operations (add, remove, move, set items, set config,
   rename, recolour, validate against C3 limits returning messages, serialize to C3 JSON). No DOM. *Test:* a
   `studio/model.test.html` page that runs assertions in the browser and prints PASS/FAIL, run during the UI check
   (there is no JS test runner in this repo; do not add one).

3. **API client and mock.** `studio/api.js` (C6 routes via `authFetch`, errors mapped to user messages) and
   `studio/mock-api.js` (same functions; `localStorage` for saved newsletters, wrapped in try/catch; preview assembled
   from fragments into the shared email shell, accent applied; simulated 300 ms latency; refine simulated). Selection
   rule as in the decisions. *Done:* `?mock=1` loads the catalog and returns a preview in the console.

4. **Page shell and design direction.** Load the `frontend-design` skill first. `studio.html` + `studio.css`: the
   three-column / two-column / tabbed-phone layouts, top bar copy, gating states (signed out, free with demo,
   premium), empty state ("Start from a ready-made newsletter" offering each topic's default as a starting point, or
   "Start empty"). *UI check:* desktop, Android, iOS screenshots of each gating state.

5. **Library.** Topics as collapsible groups (accent dot, section list with icons and one-line definitions, "Add"),
   widgets filterable by topic with premium/personal badges, custom section entry with the refine flow, search box
   across all of it. *UI check.*

6. **Canvas.** Ordered block cards (topic chip, section name, items stepper, widget config form, remove), drag and
   drop that works with mouse, touch and pen (`dnd.js`, auto-scroll near edges, drop indicator), up/down buttons,
   keyboard moves, undo toast, limit messages, name and accent controls. *UI check* including a drag on the phone
   emulators.

7. **Live preview.** Debounced preview requests, sandboxed `srcdoc` frame, Desktop (600 px) / Phone (375 px) toggle
   with the frame scaled to fit the column, size meter, warnings list, error state that keeps the last good preview.
   *UI check.*

8. **Save, switch, subscribe, delete.** "My newsletters" switcher (limit from the API), save with validation errors
   from the API mapped onto the blocks, unsaved-changes guard, subscribe toggle with a plain sentence of what it means
   ("Sent to <email> every Monday morning"), delete confirmation. *UI check.*

9. **Polish and accessibility pass.** Keyboard-only run through every flow, focus order, screen-reader labels, reduced
   motion, contrast, long names and many blocks, empty catalog groups, slow network (throttled) states. Fix what is
   found. *UI check:* final desktop, Android, iOS screenshots of the full flow.

10. **Docs.** `docs/architecture.md` (Studio: page, mock mode, C6 dependency), `docs/development.md` (how to open the
    studio with `?mock=1` locally), CLAUDE.md one line.

## UI check

Yes, at steps 4 to 9: desktop (`playwright`), Android (`playwright-mobile-android`, Pixel 7) and iOS
(`playwright-mobile-ios`, iPhone 15), all in mock mode, screenshots of each state; flag overflow, drag problems on
touch and tap targets under 44 px.

## Deploy needs (agents never deploy; this feeds the deploy request)

**hosting** only. The page is unlinked and works only in mock mode until roadmap E ships the API, so it can deploy any
day; there is nothing to roll back but the hosting release.

## Out of scope / follow-ups

- The real API, the top-bar link, and retiring `sections.html` (roadmap E and later).
- Templates gallery ("Start from: Investor's Monday", "Indie gamer"...), sharing a newsletter, duplicating one.
- Scheduling choices (day and time) and per-block French/English choice.
- A JS unit-test runner in CI.
