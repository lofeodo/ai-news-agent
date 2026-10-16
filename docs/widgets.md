# Widgets

Small data blocks that sit next to the articles in a newsletter: the AI paper of the week, a market snapshot, the
week's free games, new slicer versions. Plan: [`plans/widgets.md`](plans/widgets.md) (roadmap C of the
[newsletter platform](plans/newsletter-platform.md), contract C4).

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
