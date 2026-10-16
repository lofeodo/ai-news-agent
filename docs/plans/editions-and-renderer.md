# Plan B: Editions and the block renderer

Part of the [newsletter platform](newsletter-platform.md) (roadmap **B**). Can run in parallel with A, C, D and F.
Roadmap E depends on it. Branch `feat/editions-renderer`, one PR, one commit per step. The worker finishes with
`/ship`.

**Goal.** Make the email engine general: every weekly run saves its content as a reusable *edition* (the week's
summarized picks and runners-up per section), and emails are drawn by a *block renderer* from a *newsletter
definition* (an ordered list of blocks). Ready-made newsletters go through the new renderer with byte-identical
output, and a definition that mixes sections from several topics renders into a clean email, which is what the
studio's preview and custom-newsletter sends will use.

## Decisions already made

From the master plan: cost scales with content, not readers; AI readers see no change. Planner's assumptions (the
worker may refine them and record changes in the PR):

- **Contracts C2 (edition), C3 (definition) and C7 (email limits)** in the master plan are the target. If the worker
  must change one, it updates the master plan in this PR.
- **Byte-identical first.** Before any refactor, golden files capture today's `compose_html` output for the AI
  newsletter (all four variants) and for each of the four other topics from fixed fixture input. Every refactor step
  keeps them identical. Golden fixtures are committed under `tests/fixtures/render/`.
- **agent3 keeps pre-rendering ready-made newsletters** (`newsletter_variants` on the run doc, as today) and agent4 is
  untouched by this roadmap. Custom newsletters render at send time later (roadmap E).
- **The edition is written by agent3**, after selection and dedup and before the run doc's pools are pruned, as
  `editions/{run_id}`. `picks` = the articles the email shipped for that section (all-languages pass), `picks_en` =
  the English-only pass when it differs, `runners_up` = the remaining ranked pool (summarized articles only). Writing
  the edition is best effort: a failure logs, sets `agent3_edition_error` on the run doc, and never fails the run.
  Local mode writes `data/edition.json`.
- **Widgets in the renderer are a plug-in point.** The renderer takes a `render_widget(block, ctx)` callable. This
  roadmap ships a default that knows only `paper_spotlight` (wrapping today's `render_paper_card`); roadmap C replaces
  the default with its registry. This roadmap does not create `agents/widgets/`.
- **Mixed-topic emails:** when a definition draws sections from more than one topic, each section strip shows a small
  topic label ("Finance · Crypto & Fintech") in that topic's accent colour; the masthead uses the definition's name and
  accent; the footer shows each used topic's disclaimer once. Section markers become `<!-- SECTION:<topic>:<name> -->`
  **only** for mixed-topic emails; single-topic emails keep today's `<!-- SECTION:<name> -->` so agent4's AI section
  reorder keeps working.
- **Intro block for custom newsletters, no Claude call:** a short templated line built from the content (e.g. "This
  week: 3 stories in Crypto & Fintech, 4 in Releases & Reviews, and your custom section on SpaceX."). Ready-made
  newsletters keep the editor's note written by agent3. A cached Claude intro for custom newsletters is a follow-up.
- **Size:** the renderer returns the HTML and its byte size; `render_from_editions` adds a warning above 90 KB (Gmail
  clips around 102 KB).

## Files this roadmap owns (parallel work)

Owns: `agents/agent3_compose.py`, `agents/render/` (new package), `agents/newsletter_def.py` (new),
`agents/editions.py` (new), `tests/fixtures/render/`. Does **not** touch: `agents/agent4_send.py` (A),
`agents/topics.py` / `topics/*.yaml` (C), `agents/widgets/` (C), `agents/agent_subscriptions.py` (A, E), any page in
`public/` (A, D).

## Steps

Run `venv\Scripts\python -m pytest -q` before every commit.

1. **Golden files.** A script `tests/make_render_goldens.py` (not collected by pytest) renders today's
   `compose_html` from committed fixture input (papers, selections, intro, fixed date) for AI (four variants) and the
   four other topics into `tests/fixtures/render/*.html`; `tests/test_render_goldens.py` compares. `MAILING_ADDRESS`
   is fixed in the test. *Done:* goldens committed, test green on unchanged code.

2. **Newsletter definition.** `agents/newsletter_def.py`: dataclasses for the C3 blocks, `parse(dict) -> Definition`
   raising a `DefinitionError` with a list of human-readable errors (all C3 rules; needs the topic catalog from
   `topics.list_topic_ids()` and an injectable set of known widget ids, defaulting to `{"paper_spotlight"}`),
   `to_dict()`, and `from_topic(topic)` (intro, `paper_spotlight` for `spotlight: papers`, every category in profile
   order with its cap, share for AI). *Test:* `tests/test_newsletter_def.py` (every rule, round trip, `from_topic` for
   all five profiles).

3. **Renderer package, pure moves.** Move the email building blocks out of `compose_html` into `agents/render/`
   (`shell.py`: head, styles, masthead, TOC, footer; `blocks.py`: section strip, article card, paper card, share
   strip; `palette.py`: colours and fonts). `agent3_compose.py` re-exports the names other modules and tests import.
   No behaviour change. *Test:* goldens identical; existing agent3 tests green.

4. **Block loop.** `render.render(definition, content, look, prefs) -> RenderResult(html, size_bytes)`: walks the
   blocks in order, with the plug-in `render_widget`. `compose_html(...)` becomes a thin wrapper: build
   `from_topic(topic)`, gather content from its arguments, call `render`. *Test:* goldens identical (this is the
   step that proves ready-made newsletters are unchanged).

5. **Mixed-topic rendering.** Topic labels on section strips, per-topic accent on labels, numbering and TOC across
   topics, prefixed section markers for mixed emails only, disclaimers per used topic, empty-section handling
   ("Nothing notable this week." or the block is dropped when the definition says so), the templated intro.
   *Test:* fixture definition with AI paper spotlight + finance section + gaming section + a custom section renders:
   placeholders intact, no `{{` from article text, TOC matches sections, size reported, goldens unchanged.

6. **Editions.** `agents/editions.py`: `build_edition(topic, run_id, run_kind, week_of, intro, selected_all,
   selected_en, pools, widget_ids)` following C2 (strip fields to the C2 article shape, summarized only, size guard
   that trims runners-up if the doc would exceed 900 KB), `save(db, edition)`, `latest(db, topic)` (newest
   `release` edition of a topic, scanning `editions` by `created_at` with a limit, filter in code, no new index),
   `for_week(db, topic, week_of)`. agent3 calls `build_edition` + `save` best effort (see decisions); the runners-up
   come from the selection pools agent3 already keeps. *Test:* `tests/test_editions.py` with the fake Firestore;
   agent3 run test asserts an edition is written and that a save failure does not fail the run.

7. **Render from editions.** `render.render_from_editions(definition, editions_by_topic, pools_by_query,
   widget_data, prefs) -> RenderResult(html, size_bytes, warnings)`: a section block takes `picks` (or `picks_en`
   when the reader wants English only) then `runners_up` up to `max_items`; a missing edition or section drops the
   block with a warning; `custom_section` reads a C5 pool when given one, else drops with a warning; widgets read
   `widget_data[widget_id]`. Pure function, no Firestore. *Test:* fixtures for two editions + one pool; English-only;
   missing edition; size warning on an oversized definition.

8. **Sample render for review.** `tools/render_sample.py` writes `data/sample_mixed.html` from fixtures (a mixed
   definition) so the owner and the UI check can look at it. *UI check:* open it at desktop width and phone width
   (Android, iOS emulation) and screenshot.

9. **Docs.** ADR `docs/decisions/0003-editions-and-definitions.md` (why shared editions plus per-reader assembly:
   cost scales with topics, not readers); `docs/architecture.md` (Editions, Newsletter definitions, Renderer);
   CLAUDE.md (agent3 writes `editions/{run_id}`; `agents/render/`); `docs/repository-structure.md`.

## UI check

Yes, for the email only (step 8): the mixed-topic sample at desktop and phone widths, in the desktop, Android and iOS
browsers. Ready-made newsletters are pinned by golden files, so they need no visual check.

## Deploy needs (agents never deploy; this feeds the deploy request)

**agent3** only. Readers see nothing new (byte-identical); the only production change is a new `editions` collection
(one small doc per run). Optional owner action: a Firestore TTL on `editions.expires_at` if the worker sets one (the
recommendation is to keep editions for a year: `expires_at` = created + 400 days). Timing: any day Monday after the
7:10 AM send check to Thursday. Rollback: the previous agent3 image.

## Out of scope / follow-ups

- Rendering custom newsletters at send time and the preview endpoint (roadmap E).
- The widget registry and every widget beyond the paper card (roadmap C).
- A cached Claude-written intro for custom newsletters.
- Making the AI section reorder (agent4 `_apply_section_config`) use definitions instead of HTML markers.
- A plain-text alternative part for every email.
