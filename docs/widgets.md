# Widgets

Small data blocks that sit next to the articles in a newsletter: the AI paper of the week, a market snapshot, the
week's free games, new slicer versions. Plan: [`plans/widgets.md`](plans/widgets.md) (roadmap C of the
[newsletter platform](plans/newsletter-platform.md), contract C4). Code: `agents/widgets/`.

## What a widget is

A class registered in `agents/widgets/registry.py` with three methods: `produce(ctx, config)` fetches its data
(network allowed), `render(data, config, look)` draws one email-safe table row from it (no network, never raises),
and `sample_data()` feeds the admin gallery, the studio and the tests. Attributes say where it may be used:
`topics` (or `("*",)`), `scope` (`edition` or `personal`), `kind`, `premium`, `commercial_use`, `config_fields`,
`keys` (env vars its source needs), `max_age_hours`, `toc`.

| Kind | Produced | Widgets |
|---|---|---|
| pipeline | by existing agents; the widget only reads the run doc | `paper_spotlight` |
| external (edition) | once per topic run, by agent3 before rendering | `release_tracker`, `free_games`, `most_played`, `deals`, `trending_models`, `rates_board`, `week_ahead`, `market_snapshot`, `bgg_hot`, `out_this_week` |
| derived (edition) | once per topic run from that week's picks, one Haiku call | `number_of_week`, `crowdfunding_watch` |
| personal | at send and preview time, per distinct config, cached for the week | none yet (the stock watchlist waits for a licensed data source; the path is proven by a test-only widget) |

## The rules every widget follows

- **Bounded.** `agents/widgets/runner.py` gives each `produce` 20 s (a widget may ask for more with `timeout`:
  `market_snapshot` takes 45 s because the US Treasury needs ~17 s to answer), all of a run's widgets share 90 s
  in a pool of 4 threads, data must be JSON and at most 20 KB, and the rendered fragment at most 8 KB (Gmail clips an email at
  about 102 KB). A widget that breaks a rule gets a status (`failed`, `timed_out`, `stale`, `empty`,
  `skipped_no_key`) and is left out of that week's email. Nothing in a widget can fail a run or a send.
- **Untrusted data.** Everything from a source or a model goes through `esc()`; links through `safe_url()`. Derived
  widgets wrap articles in the shared prompt-injection guard and check the answer against the articles: the number
  of the week must appear verbatim in its article, a campaign name must appear in its article, and every link is
  that article's own URL, never one from the model.
- **One look.** `agents/widgets/ui.py` holds the primitives (dark card with the newsletter's accent bar, stat row,
  data table with real header cells, text sparkline `▁▂▃▄▅▆▇█`, deltas that carry an arrow and a sign as well as a
  colour, the attribution line). Layout tables are `role="presentation"`, fonts are set once per table, and
  tables wrap their first column so they fit a phone.
- **Honest data.** Every external widget shows its source and "as of" date; finance widgets add "For information
  only, not financial advice" and say that weekly changes are computed by us (the Bank of Canada and ECB terms ask
  for it); the rates board prints the New York Fed's reference-rate notice. Data older than a widget's
  `max_age_hours` is dropped, never shown as current, and a finance row older than a week is dropped on its own.
- **Non-commercial sources stay out of premium.** `commercial_use: False` (`most_played`, `bgg_hot`,
  `out_this_week`) keeps a widget out of `registry.catalog()` and makes `newsletter_def.parse(..., custom=True)`
  reject it. Only a free ready-made newsletter's profile may place it.
- **Keys ship disabled.** A widget whose key is not set is `skipped_no_key` and never called. Agents never handle
  secrets; the owner registers and stores keys. A key that travels in a URL (RAWG) is never repeated in an error.

## Where widgets appear

A topic profile lists its widgets (`topics/<id>.yaml`, `widgets:`; see
[configuration](configuration.md#topic-profiles)). `newsletter_def.from_topic` turns `after_intro` and
`before_sections` entries into widget blocks above the first section, and `after_sections` ones below the last;
`none` means "produce it every run for the edition, but don't show it in this newsletter". The block renderer's
default plug-in is the registry (`agents/widgets/plugin.py`), so ready-made and custom newsletters draw widgets
the same way. A widget with a `toc` entry gets a line in the table of contents (the paper's `RES`).

| Newsletter | Widgets (placement) |
|---|---|
| AI | `paper_spotlight` (after the intro; unchanged, byte for byte) |
| 3D printing | `number_of_week` (after the intro), `release_tracker` (after the sections) |
| Video games | `free_games` (after the intro), `most_played`, `deals` (after the sections) |
| Finance | `market_snapshot` (after the intro), `rates_board`, `week_ahead` (after the sections) |
| Board games | `number_of_week` (after the intro), `crowdfunding_watch` (after the sections) |

`trending_models` is in no profile (AI readers see no change); custom newsletters can use it once a profile
produces it (`placement: none` on the AI profile, roadmap E's call).

## Storage, health check and gallery

- **Edition widgets:** agent3 calls `widgets.edition.produce_for_run` after the intro is written and before the
  HTML. Data goes to `pipeline_runs/{run_id}/widgets/{widget_id}` (`{data, produced_at, status, error?}`), a
  `widgets_summary` (statuses only) goes on the run doc, and the edition's `widgets` list names the ones that
  produced. Local runs write `data/widgets/*.json`. `widgets.store.load_edition(db, run_id)` reads them back for
  custom newsletters.
- **Personal widgets:** `widgets.store.get_or_produce_personal(db, widget, config, week)` caches at
  `widget_cache/{widget_id}__{config_hash}__{week}` with `expires_at` 8 days out (needs a Firestore TTL rule), so
  readers with the same config share one fetch. Roadmap E wires it into the preview and the send.
- **Health check:** an informational "Widgets" section lists each newsletter's latest release run and every
  widget's status (`agents/widget_report.py`). It never marks a run as unhealthy.
- **Admin gallery:** `widgets.html` (not linked anywhere) shows every widget at email width with a desktop/phone
  switch, its sample render and its latest real render, which profiles place it and its last status. It reads
  `GET /auth/admin/widgets` on agent-subscriptions (`ADMIN_EMAILS` only).

## Keys

| Widget | Variable (Secret Manager, mounted on agent3) | Where to register | Notes |
|---|---|---|---|
| `bgg_hot` | `BGG_API_TOKEN` | BoardGameGeek application registration (free, non-commercial) | Never in premium; add it to `topics/boardgames.yaml` only once the owner is satisfied the free newsletter counts as non-commercial |
| `out_this_week` | `RAWG_API_KEY` | rawg.io/apidocs | Free plan is non-commercial: never in premium |

Without its key a widget is simply skipped (the health check says "skipped: no key").

## How to add a widget

1. **Check the source** (terms for an emailed newsletter that may become paid, attribution, caching, rate limits,
   key) and add a row to the sources table below. Record one trimmed response in `tests/fixtures/widgets/`.
2. **Write `agents/widgets/<id>.py`**: a `Widget` subclass decorated with `@register`; `produce` uses
   `ctx.get_json(...)` (proxy and User-Agent included) and returns small JSON with an `as_of`; `render` builds with
   `ui.card(...)` and ends with `ui.attribution(...)`; `sample_data` returns realistic data. Set `commercial_use`
   and `keys` honestly.
3. **Register it** by adding the module name to `BUILTIN` in `agents/widgets/__init__.py`.
4. **Test it** in `tests/test_widget_<id>.py` on the fixture (happy path, a broken payload, a source error). The
   catalog tests then check its sample, size and safety on their own, and `all_fixture_routes()` in
   `tests/widget_fakes.py` should learn its fixture so the end-to-end test covers it.
5. **Look at it** in the admin gallery, then place it in a profile (`widgets:` with a placement) and do a local
   `python orchestrator.py --topic <id>` to read the email at desktop and phone width.

## Yearly update: the finance calendar

`agents/widgets/finance_calendar.yaml` holds the central bank decision dates (BoC, Fed, ECB) and the US CPI and
jobs report dates that `rates_board` and `week_ahead` show, each copied from the official page linked in the file.
Each summer, when the banks publish the next year's schedule, add the dates and move `valid_until`. After
`valid_until` both widgets fail with "finance calendar ends ...; add next year's dates", which shows in the health
check's Widgets section (they are left out of the email, nothing else breaks). Today's file runs to 2027-12-16.

## Sources

Checked 2026-10-16 from a residential connection (not through the Squid proxy: agents never handle its password).
Every source below answered over plain HTTPS, which the proxy forwards like any other fetch. Fixtures in
`tests/fixtures/widgets/` are trimmed recordings of real responses, except the two files named `*_synthetic.*`,
which are built from the documented shape because the source needs a key nobody has registered yet.

| Widget | Source | Key / account | Terms (commercial use, attribution, caching) | Limits | Verdict |
|---|---|---|---|---|---|
| `paper_spotlight` | the run doc (agent1a + agent2a) | none | ours | none | **keep** (pipeline widget) |
| `trending_models` | Hugging Face Hub `api/models?sort=trendingScore` | none | public listing of public model ids and counts; attribution "Hugging Face" with links to the model pages | generous, one call | **keep** |
| `market_snapshot` | Bank of Canada Valet (`FXUSDCAD`, `FXEURCAD`, `BD.CDN.2YR.DQ.YLD`, `BD.CDN.10YR.DQ.YLD`) | none | [free use, copy and distribution](https://www.bankofcanada.ca/terms/) with attribution ("Source: Bank of Canada"), no implied endorsement; exchange rates are "for statistical and analytical purposes only" | don't hammer it; one call | **keep** |
| `market_snapshot` | US Treasury daily par yield curve CSV (2- and 10-year) | none | US federal government data, public domain | one call | **keep** |
| `market_snapshot` | ~~CoinGecko~~ (bitcoin) | demo key now expected | [terms](https://www.coingecko.com/en/api_terms): "Powered by CoinGecko" required, storing data discouraged (refresh every 24 h), the free tier is an attribution licence rather than a commercial one | 10k calls/month | **drop for v1** (needs an owner decision on a paid plan) |
| `market_snapshot` | ~~FRED~~ (WTI, US yields) | free key | allowed with attribution | 120/min | **not needed**: the Treasury gives the yields keyless; oil left out |
| `market_snapshot` | stock indices (S&P 500, Nasdaq, TSX) | n/a | exchange-licensed; no free source clearly allows newsletter redistribution | n/a | **needs owner decision** (licensed source) |
| `rates_board` | BoC Valet `V39079` (overnight target) | none | as above | one call | **keep** |
| `rates_board` | New York Fed Markets API, EFFR (carries the Fed's target range) | none | [licence for "personal or business purposes"](https://www.newyorkfed.org/privacy/termsofuse); must keep the source, not imply endorsement, and print the reference-rate notice | one call | **keep** (notice printed in the attribution line) |
| `rates_board` | ECB Data Portal, deposit facility rate `FM/D.U2.EUR.4F.KR.DFR.LEV` | none | ECB statistics may be reused free with the source cited | one call | **keep** |
| `rates_board`, `week_ahead` | `data/finance_calendar.yaml`: FOMC, BoC, ECB meeting dates; US CPI and jobs report dates | none | dates copied from the [Fed](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm), [BoC](https://www.bankofcanada.ca/2026/07/bank-canada-publishes-2027-schedule-policy-interest-rate-announcements-other-major-publications/), [ECB](https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html) and BLS ([CPI](https://www.bls.gov/schedule/news_release/cpi.htm), [jobs](https://www.bls.gov/schedule/news_release/empsit.htm)) pages | yearly owner update | **keep** (FRED release dates not needed) |
| `stock_watchlist` | none chosen | n/a | same as indices | n/a | **needs owner decision**; the personal-widget path is proven by a test-only widget |
| `free_games` | Epic Games Store `freeGamesPromotions` | none | undocumented public endpoint, no published API terms; shows only titles, dates and store links | one call | **keep**, parsed defensively, falls back to GamerPower |
| `free_games` | [GamerPower API](https://www.gamerpower.com/api-read) | none | "free for both personal and commercial use"; attribution with an active link to GamerPower.com | under 10 req/s | **keep** (fallback) |
| `most_played` | Steam Web API `ISteamChartsService/GetMostPlayedGames` + store `appdetails` for names | none | [Steam Web API terms](https://steamcommunity.com/dev/apiterms) (2010) license distribution "for their personal use via your Application", forbid "unsolicited marketing", say nothing explicit on commercial use | 100k calls/day; 11 calls a run | **keep, `commercial_use: False`** until the owner reads the terms (then flip it) |
| `deals` | [CheapShark API](https://apidocs.cheapshark.com/) | none | free; the one condition is that deal links go through CheapShark (`/redirect?dealID=`); naming CheapShark is appreciated | be gentle; 2 calls | **keep** |
| `out_this_week` | RAWG `api/games` (release dates) | owner-registered key | [free plan: non-commercial only](https://rawg.io/apidocs), attribution with an active link on every page using the data, no redistribution | 20k/month | **needs owner key**; ships disabled, `commercial_use: False` |
| `out_this_week` | ~~IGDB~~ | Twitch app credentials | commercial use needs a partnership under the Twitch developer agreement | 4 req/s | **drop** (RAWG is simpler for the same disabled slot) |
| `bgg_hot` | BoardGameGeek XML API2 `/hot` | registered app + Bearer token (since July 2025) | registration is free for non-commercial use only | courteous use | **needs owner key**; ships disabled, `commercial_use: False` (owner decision 2026-10-16: never in premium) |
| `crowdfunding_watch` | the edition's picks (one Haiku call) | Anthropic key (agent3 has it) | our own summaries | one call | **keep** |
| `release_tracker` | GitHub REST `repos/{repo}/releases/latest` for PrusaSlicer, OrcaSlicer, Bambu Studio, Cura, Marlin, Prusa-Firmware-Buddy, Creality Print | none | GitHub API terms; links to the release pages | 60/hour unauthenticated per IP; 7 calls a run | **keep** (Klipper dropped: it publishes tags, no GitHub releases) |
| `model_of_week` | ~~Thingiverse API~~ | app token | current API terms could not be found; each model carries its own Creative Commons licence, often non-commercial | unknown | **drop for v1** (Printables has no public API) |
| `number_of_week` | the edition's picks (one Haiku call) | Anthropic key | our own summaries | one call | **keep** |

Recorded fixtures: `hf_trending.json`, `boc_market.json`, `boc_policy.json`, `treasury_yields.csv`,
`nyfed_effr.json`, `ecb_dfr.json`, `epic_free_games.json`, `gamerpower.json`, `steam_most_played.json`,
`steam_appdetails.json`, `cheapshark_deals.json`, `cheapshark_stores.json`, `github_releases_latest.json`
(including Klipper's 404); synthetic: `bgg_hot_synthetic.xml`, `rawg_games_synthetic.json`.

## Follow-ups

- Stock indices and the personal stock watchlist: need a licensed data source (owner decision).
- Bitcoin in the market snapshot: CoinGecko's free tier is an attribution licence, not a commercial one.
- `most_played` is `commercial_use: False` until the owner reads Steam's Web API terms; flip it if they allow it.
- Producing `trending_models` for custom newsletters (`placement: none` on the AI profile) when roadmap E ships.
- Canada's CPI and Labour Force Survey dates in the week ahead (Statistics Canada schedule).
- Chart images, widgets inside a section, reader-configurable edition widgets, alerts when a widget fails several
  weeks in a row, and an optional GitHub token for the release tracker (60 calls an hour per IP today).
