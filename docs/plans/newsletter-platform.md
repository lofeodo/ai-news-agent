# Plan: Newsletter platform (master plan)

**Goal.** Turn the site from "one AI newsletter" into a newsletter service: anyone can subscribe free to ready-made
newsletters (AI, finance, video games, board games, 3D printing, and later more), and premium members build their
own newsletter in a studio by mixing sections from any topic, adding widgets (the paper of the week, a market
snapshot, a free-games list...) and custom sections, with a live preview of exactly what will land in their inbox.

This file is the master plan. It does not get executed by itself: it sets the architecture, the shared formats
(contracts) and the order of the individual roadmaps below, each of which is one branch and one PR. Its last section
says which roadmaps can run at the same time.

## Decisions already made

Owner's answers (2026-10-16):

- **Launch per topic.** Signup and weekly sending get built for all four new topics, but each topic stays hidden
  from the public until the owner switches it on after reading a real weekly draft.
- **Ready-made newsletters are free; the builder is premium.** Free: subscribe to any public ready-made newsletter.
  Premium: the studio (mix sections across topics, widgets, custom sections, own name and colour).
- **Payments come later, in their own roadmap.** The studio first opens by invite (the existing premium email list)
  so real people can test it. A payments plan (Stripe) is written after that, once the owner has seen it work.
- **Roadmaps should run in parallel where possible.** Dependencies are allowed when they are real; otherwise each
  roadmap codes against the contracts in this file, owns a clear set of files, and binds to other roadmaps' work as
  late as possible.

Planner's assumptions (each roadmap restates the ones it relies on):

- **The AI newsletter's readers see no change** from any roadmap unless that roadmap says otherwise in its goal. The
  existing equivalence and composition tests stay green everywhere.
- **Cost scales with content, not with readers.** Claude is called once per topic per week (as today) and once per
  *unique* custom topic per week. Assembling one reader's newsletter is code, not a Claude call; any per-newsletter
  Claude call (e.g. an intro for a custom newsletter) is cached per distinct definition and capped by a weekly
  budget.
- **One site, one account, many newsletters.** Same domain and sign-in; each newsletter has its own name, accent
  colour, landing page and archive. The current brand (Latent SpaceMail) stays the AI newsletter's name. A platform
  brand or domain is an owner decision for later (follow-up), not part of these roadmaps.
- **Weekly cadence for everything.** Every newsletter is drafted on Sunday and sent Monday morning, like the AI one.
  Daily or custom schedules are a follow-up.
- **Agents never deploy, never touch production data, secrets, Scheduler jobs or subscriber sends.** Each roadmap
  ends with a deploy request listing what the owner runs, including new Scheduler jobs and any API keys a widget
  needs.

## Architecture: shared content, assembled per reader

```
                 per topic, weekly (exists today)                      new
 sources ──> agent1a/1b ──> agent2a/2b ──> agent3 ──┬──> pre-rendered issue of the ready-made newsletter
                                                     │      (variants, as today) ──> agent4 sends it
                                                     └──> EDITION: the week's ranked, summarized articles per
                                                          section + edition widget data   (roadmap B, C)

 custom topics, weekly:  unique custom section topics ──> fetch + filter + summarize ──> CUSTOM POOLS (roadmap F)

 per reader, at preview and send time (no Claude call):
   NEWSLETTER DEFINITION (ordered blocks) + this week's EDITIONS + CUSTOM POOLS + widget data
        ──> block renderer ──> email HTML ──> studio live preview / agent4 send     (roadmaps B, D, E)
```

- **Topic profiles** (`topics/<id>.yaml`, exists) say how to *gather* a topic's content.
- **An edition** is one topic's content for one week: per section, the editor's ranked picks plus runners-up, each
  already summarized, plus that week's edition-widget data. It is produced by the topic's normal weekly run, so the
  expensive work happens once per topic regardless of how many custom newsletters reuse it.
- **A newsletter definition** says how to *assemble* an email: an ordered list of blocks (intro, sections drawn from
  any topic, widgets, custom sections, share strip) plus a name and colour. Every ready-made newsletter has a
  definition derived from its topic profile; a custom newsletter is a definition a premium member saved.
- **Widgets** are small data blocks with their own data source (not articles): the AI paper of the week is the first
  one. Edition widgets are produced once per topic per week; personal widgets (e.g. a stock watchlist) are produced
  at send time per distinct configuration, with a cache.
- **The block renderer** turns a definition plus content into email-safe HTML. Ready-made newsletters keep being
  pre-rendered by agent3 (as today); custom newsletters are rendered by agent4 at send time and by the studio's
  preview endpoint.

## Shared contracts

Every roadmap codes against these. A roadmap that needs to change one updates this section in its own PR and says so
in the PR description; roadmaps that already merged are then adjusted by whichever merges second.

### C1. Newsletter ids and subscriptions

- **Newsletter id:** a ready-made newsletter's id is its topic id (`ai`, `finance`, `gaming`, `boardgames`,
  `printing3d`). A custom newsletter's id is `c_` + 20 random URL-safe characters.
- **Subscriber doc** (`subscribers/{email}`, exists) gains `newsletters: [<id>, ...]` (the newsletters this address
  is actively subscribed to) and `newsletter_meta: {<id>: {"subscribed_at", "consent_text_version"}}`. The legacy
  `active` flag keeps meaning "subscribed to the AI newsletter" and is kept in sync with `"ai" in newsletters`, so
  agent4's AI send query (`active == True`) and the AI preferences flow are unchanged. Other newsletters are queried
  with `where("newsletters", "array_contains", <id>)` (a single-field query, no new index). Helper module:
  `agents/subscriptions_model.py` (`subscribed_ids(doc)`, `is_subscribed(doc, id)`, `add(...)`, `remove(...)`),
  owned by roadmap A.
- **Links in emails:** unsubscribe and preferences links gain `&n=<newsletter id>`; a link without `n` means `ai`
  (every email already sent keeps working). The unsubscribe page offers "unsubscribe from this newsletter" and
  "unsubscribe from everything".
- **Custom newsletters** are mailed only to their owner's verified account email (no sharing in v1), so a custom
  newsletter's subscriber list is its owner.
- **Added by roadmap A (merged):** the doc also carries `subscribed_any` (true while it gets any newsletter; the
  `MAX_SUBSCRIBERS` cap counts people with it) and, while an email-form addition waits for its confirmation click,
  `pending_newsletters` / `pending_consent_version` / `pending_requested_at`. `active` wins for AI when it disagrees
  with the list. `newsletter_meta.<id>` gains `unsubscribed_at` on leaving. Always write through
  `subscriptions_model.add()` / `remove()` / `remove_all()`, which return the fields to write.
  `GET /newsletters` returns `{"newsletters": [{id, name, tagline, accent_color, spotlight, sections: [{name, icon}],
  latest_issue, hidden}], "consent_text", "consent_text_version"}`; hidden topics only for admins (`agents/launch.py`).

### C2. Edition

One Firestore doc per composed run, `editions/{run_id}` (same id as `pipeline_runs/{run_id}`, so debug runs never
collide with release runs), produced by agent3 after selection:

```json
{
  "topic": "finance", "run_id": "...", "run_kind": "release", "week_of": "2026-10-26",
  "created_at": "2026-10-25T16:40:12Z", "intro": "editor's note text",
  "sections": {
    "Crypto & Fintech": {
      "icon": "🪙",
      "picks":      [ARTICLE, ...],
      "runners_up": [ARTICLE, ...],
      "picks_en":   [ARTICLE, ...]
    }
  },
  "widgets": ["paper_spotlight"]
}
```

`ARTICLE` = `{"title", "url", "summary", "source", "language", "published_at", "points", "points_source"}` (only
fields that already exist on summarized articles; missing values are `null`). `picks_en` is present only when the
topic has French articles (AI). Only articles that have a summary may appear. The latest edition of a topic is the
newest `release` edition of that topic (helper `editions.latest(db, topic)`), owned by roadmap B. Must stay well
under 1 MiB (it holds only picks and runners-up, ~10 articles per section).

### C3. Newsletter definition

```json
{
  "version": 1,
  "name": "My Monday brief",
  "accent_color": "#7fd1a0",
  "blocks": [
    {"type": "intro"},
    {"type": "widget", "widget": "paper_spotlight", "topic": "ai", "config": {}},
    {"type": "section", "topic": "finance", "section": "Crypto & Fintech", "max_items": 3},
    {"type": "section", "topic": "gaming", "section": "Releases & Reviews", "max_items": 4},
    {"type": "custom_section", "title": "SpaceX", "query": "SpaceX product launches & mission updates", "max_items": 3},
    {"type": "share"}
  ]
}
```

Rules (validator `agents/newsletter_def.py`, owned by roadmap B): at most 12 blocks, 8 section blocks, 3
custom sections, 4 widgets; `max_items` 1 to 5; one `intro` at most, always first if present; `share` at most once;
colour `#rrggbb`; name 1 to 60 characters with no angle brackets; unknown block types or fields are rejected.
`topic` + `section` must name a section of a known topic; the widget must be in the widget catalog and allowed for
the newsletter (personal widgets need premium; a widget with `commercial_use: False` is rejected in every custom
newsletter, because custom newsletters are the paid product). A widget block's `topic` says whose edition supplies the data; it is
omitted for personal widgets. Section blocks from different topics may share a name; the renderer
shows the topic name as a small label when a newsletter mixes topics.

A ready-made newsletter's definition is derived from its profile (`newsletter_def.from_topic(topic)`): intro, the
topic's widgets at their placement, every category in profile order with its cap, and share (AI only, as today).

### C4. Widget

Python interface in `agents/widgets/` (owned by roadmap C):

```python
class Widget:
    id: str               # "market_snapshot"
    name: str             # "Market snapshot"
    description: str      # one sentence, shown in the studio
    topics: tuple         # topic ids it belongs to, or ("*",) for any
    scope: str            # "edition" (same for every reader, produced once per topic run) or "personal"
    premium: bool         # personal widgets are always premium
    commercial_use: bool  # False when the data source's terms forbid commercial use (BoardGameGeek):
                          # such a widget may appear only in free ready-made newsletters, never in a custom one
    config_fields: tuple  # [{"name", "type": "tickers"|"choice"|"int"|"bool", "label", "options"?, "max"?, "default"}]
    def produce(self, ctx, config) -> dict      # network allowed, bounded time, JSON-serializable, <= 20 KB
    def render(self, data, config, look) -> str  # email-safe HTML fragment, no network, never raises on odd data
    def sample_data(self) -> dict               # for the studio preview, the admin gallery and tests
```

Edition widget data is stored at `pipeline_runs/{run_id}/widgets/{widget_id}`; personal widget data at
`widget_cache/{widget_id}__{config_hash}__{week}` with `expires_at` (Firestore TTL). A widget that fails to produce
is left out of that week's email; it never fails a run or a send.

### C5. Custom pool

Produced weekly per unique custom section query (roadmap F), `custom_pools/{query_hash}__{week}`:
`{"query", "week_of", "created_at", "picks": [ARTICLE, ...], "runners_up": [ARTICLE, ...]}`, where `query_hash` is
`sha256(normalized query)[:16]` (normalized = lowercased, whitespace collapsed).

### C6. Studio API (served by agent-subscriptions; mocked by roadmap D, implemented by roadmap E)

| Route | Who | Body / result |
|---|---|---|
| `GET /catalog` | public | `{"topics": [{"id","name","tagline","accent_color","public","sections": [{"name","definition","icon"}]}], "widgets": [{"id","name","description","topics","scope","premium","config_fields"}]}` (only public topics, plus hidden ones for admins; widgets with `commercial_use: False` are never listed) |
| `GET /auth/newsletters` | signed in | `{"newsletters": [{"id","definition","subscribed","updated_at"}], "limit": 3}` |
| `POST /auth/newsletters` | premium | `{"id"?, "definition"}` → `{"id","definition"}` (create or update; 400 with `{"errors": [...]}` on an invalid definition) |
| `DELETE /auth/newsletters/{id}` | owner | `{"deleted": true}` |
| `POST /auth/newsletters/preview` | premium | `{"definition"}` → `{"html","size_bytes","warnings": [...]}` (rendered from the latest editions, widget sample data for personal widgets) |
| `POST /auth/newsletters/{id}/subscribe` and `/unsubscribe` | owner | `{"subscribed": bool}` |

Every `/auth/*` route takes `Authorization: Bearer <Firebase ID token>`, as today. The preview HTML is shown in a
sandboxed iframe via `srcdoc` with the existing `PREVIEW_CSP`.

### C7. Email limits every renderer respects

Table layout, inline styles, no script, no web fonts that break the layout if blocked, https images only, alt text,
readable in dark mode. Gmail clips an email over about 102 KB of HTML: the AI variants are already near that (the
run doc note puts four variants at ~420 KB), so the renderer reports `size_bytes`, the preview warns above 90 KB and
the definition limits above keep custom newsletters under it.

## The roadmaps

| Id | Plan | What it delivers | Depends on |
|---|---|---|---|
| **A** | [`topic-signup-and-sending.md`](topic-signup-and-sending.md) | Weekly release runs, signup, per-newsletter unsubscribe, sending and health check for the four new topics, each behind a launch switch. Contract C1. | nothing |
| **B** | [`editions-and-renderer.md`](editions-and-renderer.md) | Editions (C2), newsletter definitions (C3) and the block renderer; agent3 renders ready-made newsletters through it, byte-identical. | nothing |
| **C** | [`widgets.md`](widgets.md) | Widget framework (C4), the paper of the week as the first widget, a researched catalog of widgets per topic, admin widget gallery. | nothing; only its integration step (15) binds to B, with a fallback if B has not merged |
| **D** | [`newsletter-studio-ui.md`](newsletter-studio-ui.md) | The studio: build, reorder and live-preview a custom newsletter, desktop and phone, against a mock of C6. | nothing (mock API) |
| **F** | [`custom-topic-sourcing.md`](custom-topic-sourcing.md) | Weekly articles for custom sections ("SpaceX"), with a budget cap (C5). | nothing |
| **E** | [`custom-newsletters.md`](custom-newsletters.md) | The real C6 API, saving and previewing custom newsletters, and agent4 sending them; switches the studio from mock to real. | **A and B**; uses C, D, F if merged (each optional) |
| **G** | not written yet | Payments (Stripe), plans and the upgrade flow. Written after the studio has been used by invited members. | E |

## Running roadmaps in parallel

```
week 1 ──────────────────────────────────────────────>      later
 A  topic signup & sending      ─────────────┐
 B  editions & block renderer   ─────────────┼──> E  custom newsletters ──> (G payments, own plan)
 C  widgets          ───────────────(binds to B late)─┘   ▲
 D  studio UI (mock API) ─────────────────────────────────┤
 F  custom topic sourcing ────────────────────────────────┘
```

**A, B, C, D and F can all start now, in parallel.** E starts when A and B have merged. To keep five parallel PRs from
colliding:

| File or area | Owner (only this roadmap edits it) | Others |
|---|---|---|
| `agents/agent4_send.py` | A, then E | B, C, D, F: do not touch |
| `agents/agent3_compose.py` | B | C: only in its last integration step, after rebasing on B if B merged |
| `agents/topics.py`, `topics/*.yaml` | C (adds `widgets:`) | A: only deletes the "non-AI topics are debug-only" refusal in `check_run_request` |
| `agents/agent_subscriptions.py` | A (subscriptions), then E (studio routes) | C, F: new routes go in their own router module mounted in `main.py` |
| Top bar markup in `public/newsletter/*.html`, `nav.js` | A | D and E: no top-bar edits; E adds the Studio link after A merged |
| `agents/agent_healthcheck.py` | A | C, E, F: add a section through a small function in their own module, wired by a one-line call in their integration step |
| `main.py`, `cloudbuild.yaml`, `CLAUDE.md`, `docs/*.md` | shared | small, additive edits only; rebase before `/ship` and resolve by keeping both sides |

Rules for every worker in a parallel lane:

1. **Read this file first**, then `git fetch` and check which of the other roadmaps have merged; build on what is
   there. If a contract here changed in a merged PR, follow the merged version.
2. **Prefer new modules** over edits to big shared files; keep unavoidable edits small and additive.
3. **Rebase on `origin/main` before marking the PR ready** and re-run the full suite.
4. **Never assume another roadmap's code exists** unless it has merged: code against the contract, test with fakes
   or fixtures, and leave a clearly named integration step for the moment both are on `main`.

## Deploy order across roadmaps

Each roadmap has its own deploy request. Across them: A's sending can deploy any week; B is invisible to readers
(byte-identical) and can deploy any week; C's widgets appear in an email only when a topic profile lists them (the
AI paper card is unchanged); D ships a page nobody is linked to until E; E is the first deploy that sends custom
newsletters. Never deploy between Sunday 12:00 PM and the Monday send check (7:10 AM); Monday after 7:10 AM to
Thursday is the window.

## Out of scope / follow-ups (not in any roadmap yet)

- Payments and plans (roadmap G, written after the studio has been tried by invited members).
- A platform brand and home page for the whole service (the AI landing page stays the home page until then).
- Daily or custom send days; time zones per reader.
- Sharing a custom newsletter with other people, or publishing one as a public newsletter.
- Retiring the old premium "Customize" page (AI section reorder) once the studio covers it; moving its saved
  configs into custom newsletters.
- French versions of the new topics.
- Reddit as a source (needs an app registration by the owner).
- A per-reader consent and privacy review across the service (the open "Optional A" privacy item, CASL / Law 25),
  before payments.
