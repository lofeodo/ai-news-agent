# 0003 — Shared editions, assembled per reader by a block renderer

Status: accepted (2026-10-16)

## Context

The site is becoming a newsletter service (master plan: `docs/plans/newsletter-platform.md`): free ready-made
newsletters per topic, and premium members who build their own by mixing sections from several topics, widgets and
custom sections. Until now agent3 drew one email per topic straight from that run's selection, in one long
`compose_html` template. A custom newsletter cannot work that way: running selection, summaries or an editor's note
per reader would make cost grow with the number of readers, and the template knew only one topic per email.

## Decision

Split *gathering* content from *assembling* an email.

- **Edition** (`agents/editions.py`, contract C2): agent3 saves each composed run's content as `editions/{run_id}`:
  per section, the picks the email shipped, the English-only picks when they differ, and the ranked runners-up,
  every article already summarized and stripped to a small fixed shape. The expensive work (fetch, filter,
  summarize, select) still happens once per topic per week; any number of newsletters reuse the edition.
- **Newsletter definition** (`agents/newsletter_def.py`, contract C3): an ordered list of blocks (intro, sections
  from any topic, widgets, custom sections, share) plus a name and colour, with a validator. Every ready-made
  newsletter's definition is derived from its topic profile (`from_topic`).
- **Block renderer** (`agents/render/`): one pure function, `render(definition, content, look, prefs)`, walks the
  blocks; `render_from_editions(...)` builds a custom newsletter from editions, custom pools and widget data with no
  Claude call and no Firestore access. Widgets are a plug-in (`render_widget(block, ctx)`), so the widget roadmap
  can add its registry without touching the loop.

Constraints kept on purpose:

- **Ready-made emails are byte-identical.** Golden files of today's output (AI × 4 variants, the four other
  topics) were committed before any refactor and are compared byte for byte (`tests/test_render_goldens.py`).
  agent3 still pre-renders the ready-made variants; agent4 is unchanged.
- **Single-topic emails keep `<!-- SECTION:<name> -->`** so agent4's AI section reorder keeps working; only emails
  that mix topics use `<!-- SECTION:<topic>:<name> -->`.
- **The edition write is best effort.** A failure is logged and recorded as `agent3_edition_error` on the run doc;
  it never fails the run (the weekly email matters more than the edition).
- **Custom newsletters get a templated intro** ("This week: 3 stories in …"), not a Claude call per reader.

## Consequences

Cost scales with topics (and unique custom queries), not readers. One small new Firestore doc per composed run
(`editions`, `expires_at` 400 days for an optional TTL rule). Two renderers would have drifted, so ready-made and
custom emails share one code path, and any change to the email's HTML now shows up as a golden-file diff that has
to be regenerated deliberately (`python tests/make_render_goldens.py`).
