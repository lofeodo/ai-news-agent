# Style guide

The look of everything a reader sees: the site (`public/newsletter/`), the email (`agents/render/`), and the
health-check report. Read this before any UI change, and change this file when a rule changes. The checks that
enforce it are listed at the end.

## Design philosophy

**Dark editorial dispatch: industrial zine meets hacker terminal.** One warm accent on near-black surfaces,
monospace type everywhere, terminal motifs (`❯` prompts, `>_` eyebrows, `TRANSMISSION`), hairline borders, and
left accent bars instead of boxes. It should feel like a well-set terminal, not a SaaS dashboard.

Rules that follow from it:

- **One typeface family for text: monospace.** Headings, body, forms, links, the email. Hierarchy comes from
  size, weight, colour and letter-spacing, never from switching to another family.
- **Bebas Neue is the only other face,** and only for *labels*: buttons, eyebrows, section headers, badges, pills.
  Always uppercase with wide tracking. Never for sentences.
- **No serif, anywhere.** A serif display face (Cormorant Garamond / EB Garamond) was defined for months and, once
  its files actually loaded, looked out of place in every heading it touched. It was removed on 2026-10-29 and a
  test keeps it out. If a heading "looks funny", it is almost always a stray family or an oversized serif.
- **One accent.** The warm amber `#c8b89a` marks what matters: the primary button, the active nav link, the
  prompt, left bars, focus rings. Green and red exist only for success and error states. Nothing else is coloured.
- **Restraint.** Secondary text is muted grey, borders are one hairline, cards are flat with at most a faint glow.
  New components reuse the tokens below; a new colour or font is a design decision, not a styling detail.

## Fonts

Self-hosted in `public/newsletter/fonts/` and declared in `fonts.css` (the `latin` subset, which covers French
accents). A test checks each file really contains basic Latin and matches its `@font-face`.

| Role | Token | Stack | Where |
|---|---|---|---|
| Text, headings, forms (site) | `--font` (`style.css`), `--mono` (`landing.css`) | IBM Plex Mono 400/700 on the landing page; `Menlo, Cascadia Code, Consolas, Courier New, monospace` on the other pages | Everything that is not a label |
| Labels | `--label` | Bebas Neue 400, `Arial Narrow, Arial, sans-serif` | Buttons, eyebrows, `.prefs-section__header`, badges, top-bar pills |
| Email and report | `palette.F` | `Menlo, Cascadia Mono, Consolas, Courier New, monospace` | Every element of the email (no web fonts in email) |

Known inconsistency, left as a follow-up: the landing page's stack starts with the self-hosted IBM Plex Mono, the
other pages' `--font` and `topbar.css` start with the system Menlo stack. Aligning them is a one-line change per
file once the owner wants the app pages to switch.

## Colours

Site tokens (`style.css` and `landing.css` `:root`; `topbar.css` repeats the hex values):

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0f0f0f` | Page background (behind the canvas) |
| `--surface` | `#161616` | Cards (`rgba(22,22,22,.97)`), panels |
| `--border` | `#2a2a2a` | Every hairline |
| `--text` | `#e8e8e8` | Body text, headings |
| `--soft` (landing) | `#b4b0a8` | Lede text |
| `--muted` | `#8b8b8b` | Secondary text, notes, placeholders, inactive nav |
| `--accent` | `#c8b89a` | Primary buttons, active/hover links, prompt, left bars, focus |
| `--amber` (landing) | `#d9a441` | The "paper of the week" bar in the issue excerpt only |
| `--success` | `#7ec8a0` | Success messages, the "Subscribed" tick |
| `--error` | `#e07070` | Error messages, the unsubscribe outline |

Email palette (`agents/render/palette.py`): darks `#080808` (header/footer), `#111111`, `#191919`, `#222222`
(paper card); lights white (article cards) and cream `#faf9f4` (intro); ink `#111111` / `#555555` on white, ash
`#d4cfc8` / char `#6a6560` on dark; accents amber `#c8b89a`, gold `#d4a843` (scores), HN green `#22c55e`.
Link colour on white cards is a darkened accent (`darken()`), never the accent itself (too light on white). The
report (`agents/report_html.py`) reuses the email palette.

## Type scale

| Element | Size | Weight / tracking | Colour |
|---|---|---|---|
| Page title (`h1` with `❯` prompt) | 1.05rem | normal, 0.04em | accent |
| Landing hero `h1` | `clamp(2rem, 3.6vw, 2.9rem)` | 700, −0.02em | text, `.hl` span in accent |
| Section or card heading (`.prefs-card__headline`, `.welcome__status`, newsletter name) | 1rem to 1.15rem | normal or 600, 0.03 to 0.04em | text |
| Hub / newsletter page titles | `clamp(1.5rem, 5vw, 2rem)` to `clamp(1.6rem, 6vw, 2.4rem)` | 600, 0.02em | text or the newsletter's accent |
| Body, subtitles | 0.88 to 0.9rem, line-height 1.6 | normal | text / muted |
| Notes, taglines, consent lines | 0.76 to 0.84rem | normal | muted |
| Labels (Bebas) | 0.58rem (badges) to 0.95rem (primary buttons) | uppercase, 0.1 to 0.2em | depends on component |

Headings never go above about 1.15rem inside a card; big type belongs to the landing hero and the newsletter
pages only.

## Components

- **Card** (`.card`): max-width 520px, surface background, hairline border, 4px accent left bar, 40/36px padding
  (less on phones). App pages are one centred card.
- **Inset card** (`.prefs-card`, `.upgrade-gate`): a quieter card inside the card. Faint accent tint
  `rgba(200,184,154,.04)`, hairline accent border at 18 % alpha, 3px accent left bar, 18px padding. For the one
  thing the page wants the reader to do (subscribe, upgrade).
- **Primary button** (`button[type=submit]`, `.btn`, `.btn-primary-action`, `.welcome__subscribe`): full width,
  accent background, `--bg` text, Bebas 0.95rem, 0.18em tracking, 14px padding. One per view.
- **Google button** (`.btn-google`): ghost, `rgba(255,255,255,.04)` fill, hairline border, monospace text, the
  Google "G" at 18px. Text says what it does: "Subscribe with Google" where it subscribes, "Continue with Google"
  where it only signs in.
- **Secondary links** (`.member__link`, `.btn-email-link`, `.btn-secondary-link`): muted monospace, centred,
  accent on hover, no underline.
- **Danger** (`#unsub-btn`): transparent with a 1px error border and error text, uppercase monospace.
- **Pills** (top bar `.topbar__sub`, `.topbar__auth-tier`, `.upgrade-gate__badge`): Bebas or small-caps
  monospace, 0.58 to 0.62rem, 0.1 to 0.14em tracking, 2–3px vertical padding. Filled accent when actionable
  (`Subscribe`), outlined green when informational (`✓ Subscribed`), filled accent for the Premium badge.
- **Toggle switch** (`.toggle-switch`): the only control that saves on change; 44px tap target.
- **Check row** (`.check-row`): square box, label in text colour, description in muted below.
- **Messages** (`.msg`): hidden until `.visible`; `.success` green, `.error` red; placed under the control they
  describe.
- **Top bar**: 48px, logo, nav links (muted monospace 0.72rem, 0.08em; active and hover in accent), account area
  (dot, email, pills, sign out), Share pill, GitHub pill. On phones only the active nav link shows and the bar
  scrolls horizontally.

## Layout and responsiveness

- Phone gutters 16px (12px in the top bar); no horizontal page scroll (check `scrollWidth === innerWidth`).
- Tap targets 44px (switches, inputs in the newsletter signup).
- The landing page is a two-column hero (copy and signup left, issue excerpt right) that stacks on phones; the
  signed-in state swaps the signup for a status line and one primary button.
- Every page has the canvas background (`bg.js`, drifting envelopes) at `z-index: 0` with content above it; it
  respects `prefers-reduced-motion`.
- Motion: 0.15 to 0.2s transitions on colour, border and shadow only. No entrance animations.

## Copy

- Say what a button does ("Subscribe with Google", "Send preferences link"), not where it goes.
- Status first, then the action: "Signed in as x, not subscribed yet." above a Subscribe button.
- One email a week, unsubscribe any time: the consent line under every subscribe control.
- Errors are one sentence and tell the reader what to do next.

## Email

Table-based, inline styles, monospace only, dark header and footer with white article cards and a cream intro.
The ready-made emails are pinned byte for byte by `tests/fixtures/render/` goldens: a visual change to the email
is a deliberate golden regeneration (`python tests/make_render_goldens.py`), never a side effect.

## Checklist for a UI change

1. Use an existing token and component; if neither fits, add the token here first.
2. No new font family, no serif, no new colour.
3. Labels in Bebas, everything else monospace.
4. Screenshots on desktop, Pixel 7 and iPhone 15 (`CLAUDE.md`, Browser Testing); no overflow on either phone.
5. Run `pytest`: `tests/test_site_fonts.py` fails on a serif family or the old `--display` / `--body-serif`
   tokens anywhere under `public/newsletter/`, and checks the font files; `tests/test_site_pages.py` pins the
   subscribe-flow pages.
