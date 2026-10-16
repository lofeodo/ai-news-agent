"""Regenerate the studio's mock fixtures: python tests/make_studio_fixtures.py

Not collected by pytest (the name does not start with test_). The newsletter studio (public/newsletter/studio.html)
runs against a mock of the studio API (contract C6 in docs/plans/newsletter-platform.md) until roadmap E ships the
real routes. The mock needs three things, all written here so they cannot drift from the real code:

- fixtures/catalog.json       the C6 `GET /catalog` answer, built from topics/*.yaml plus the planned widget
                              catalog (docs/plans/widgets.md; BoardGameGeek is left out, it is never in premium)
- fixtures/fragments/*.html   email templates drawn by the real block renderer (agents/render/) with placeholder
                              values (__NAME__, the accent #abcdef...), which mock-api.js fills in
- fixtures/mock-content.json  renderer constants the mock repeats (topic labels, icons, disclaimers, size limit)

The hand-written sample articles (fixtures/sample-articles.json) and example definitions
(fixtures/definitions/*.json) are not generated; tests/test_studio_fixtures.py checks them.
tests/test_studio_fixtures.py also rebuilds everything above and compares, so a renderer or profile change that
is not regenerated here fails the suite.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agents"))

STUDIO_DIR = os.path.join(ROOT, "public", "newsletter", "studio")
FIXTURE_DIR = os.path.join(STUDIO_DIR, "fixtures")
FRAGMENT_DIR = os.path.join(FIXTURE_DIR, "fragments")
CATALOG_PATH = os.path.join(FIXTURE_DIR, "catalog.json")
MOCK_CONTENT_PATH = os.path.join(FIXTURE_DIR, "mock-content.json")
SAMPLES_PATH = os.path.join(FIXTURE_DIR, "sample-articles.json")
DEFINITIONS_DIR = os.path.join(FIXTURE_DIR, "definitions")

# Placeholder colours the mock swaps for the newsletter's accent, its darkened link colour, and (in an email that
# mixes topics) each section's topic accent. None of them appears in the renderer's own palette.
ACCENT = "#abcdef"
LINK = "#012345"
TOPIC_ACCENT = "#fedcba"
SAMPLE_URL = "https://example.invalid/__URL__"

# The planned widget catalog (docs/plans/widgets.md, "Starting catalog"), in the C6 shape. Roadmap C builds the
# real registry; when it merges, GET /catalog serves the registry and this list only feeds the mock.
# `bgg_hot` is not here: its source forbids commercial use, so the studio never lists it.
WIDGETS = [
    {"id": "paper_spotlight", "name": "Paper of the week",
     "description": "The week's most interesting AI research paper, with a short hook and a link to read it.",
     "topics": ["ai"], "scope": "edition", "premium": False, "config_fields": []},
    {"id": "trending_models", "name": "Trending on Hugging Face",
     "description": "The models climbing fastest on the Hugging Face Hub this week.",
     "topics": ["ai"], "scope": "edition", "premium": False,
     "config_fields": [{"name": "count", "type": "int", "label": "Models shown", "max": 8, "default": 5}]},
    {"id": "market_snapshot", "name": "The week in markets",
     "description": "Where the dollar, bond yields, oil and bitcoin closed the week, and how far they moved.",
     "topics": ["finance"], "scope": "edition", "premium": False, "config_fields": []},
    {"id": "rates_board", "name": "Central bank rates",
     "description": "Policy rates of the Fed, the Bank of Canada and the ECB, with each one's next meeting.",
     "topics": ["finance"], "scope": "edition", "premium": False, "config_fields": []},
    {"id": "week_ahead", "name": "The week ahead",
     "description": "Economic releases and central bank meetings coming up in the next seven days.",
     "topics": ["finance"], "scope": "edition", "premium": False, "config_fields": []},
    {"id": "stock_watchlist", "name": "My watchlist",
     "description": "Your own list of tickers with their weekly close and change.",
     "topics": ["finance"], "scope": "personal", "premium": True,
     "config_fields": [{"name": "tickers", "type": "tickers", "label": "Tickers", "max": 8,
                        "default": ["SHOP", "RY", "AAPL"]}]},
    {"id": "free_games", "name": "Free this week",
     "description": "Games you can claim for free this week, and until when.",
     "topics": ["gaming"], "scope": "edition", "premium": False,
     "config_fields": [{"name": "store", "type": "choice", "label": "Stores",
                        "options": ["All stores", "Epic Games Store", "Steam", "GOG"], "default": "All stores"}]},
    {"id": "most_played", "name": "Most played on Steam",
     "description": "The games with the most players on Steam this week.",
     "topics": ["gaming"], "scope": "edition", "premium": False,
     "config_fields": [{"name": "count", "type": "int", "label": "Games shown", "max": 10, "default": 5}]},
    {"id": "deals", "name": "Best deals",
     "description": "The deepest discounts on well-reviewed PC games this week.",
     "topics": ["gaming"], "scope": "edition", "premium": False,
     "config_fields": [{"name": "max_price", "type": "int", "label": "Price under (USD)", "max": 70,
                        "default": 20}]},
    {"id": "out_this_week", "name": "Out this week",
     "description": "Notable games releasing in the coming week, by platform.",
     "topics": ["gaming"], "scope": "edition", "premium": False,
     "config_fields": [{"name": "platform", "type": "choice", "label": "Platform",
                        "options": ["All platforms", "PC", "PlayStation", "Xbox", "Switch"],
                        "default": "All platforms"}]},
    {"id": "crowdfunding_watch", "name": "On Kickstarter & Gamefound",
     "description": "Board game campaigns in the week's news: where they run and how far along they are.",
     "topics": ["boardgames"], "scope": "edition", "premium": False,
     "config_fields": [{"name": "include_ended", "type": "bool", "label": "Include campaigns that just ended",
                        "default": False}]},
    {"id": "release_tracker", "name": "New versions",
     "description": "New releases of the popular slicers and printer firmwares, with what is new this week.",
     "topics": ["printing3d"], "scope": "edition", "premium": False, "config_fields": []},
    {"id": "model_of_week", "name": "Model of the week",
     "description": "One standout printable model, with its designer and a link to the files.",
     "topics": ["printing3d"], "scope": "edition", "premium": False, "config_fields": []},
    {"id": "number_of_week", "name": "Number of the week",
     "description": "One striking number from the week's stories, with the sentence it came from.",
     "topics": ["*"], "scope": "edition", "premium": False, "config_fields": []},
]


def build_catalog() -> dict:
    """The C6 `GET /catalog` answer as an admin would see it (hidden topics included, with public: false)."""
    import launch
    import topics

    out = []
    for tid in launch.ordered(topics.list_topic_ids()):   # the site's order: AI first, then by name
        t = topics.get_topic(tid)
        out.append({
            "id": t.id, "name": t.name, "tagline": t.tagline, "accent_color": t.accent_color,
            # As launched today (no PUBLIC_TOPICS): only the AI newsletter. Fixed here, not read from the
            # environment, so the fixture is the same on every machine.
            "public": t.is_default,
            "sections": [{"name": c.name, "definition": c.definition, "icon": c.icon} for c in t.categories],
        })
    return {"topics": out, "widgets": WIDGETS}


def build_mock_content() -> dict:
    """Constants of agents/render/ the mock's assembler repeats, read from the renderer so they stay in step."""
    import topics
    from render import engine, from_editions

    return {
        "topic_labels": dict(engine.TOPIC_LABELS),
        "disclaimers": {tid: topics.get_topic(tid).disclaimer for tid in topics.list_topic_ids()
                        if topics.get_topic(tid).disclaimer},
        "default_icon": engine.DEFAULT_ICON,
        "custom_icon": engine.CUSTOM_ICON,
        "custom_label": engine.CUSTOM_LABEL,
        "toc_label_max": engine.TOC_LABEL_MAX,
        "widget_toc": {k: list(v) for k, v in engine.WIDGET_TOC.items()},
        "tagline": "Your weekly briefing",
        "intro_label": "THIS WEEK",
        "empty_text": "Nothing notable this week.",
        "size_warning_bytes": from_editions.SIZE_WARNING_BYTES,
        "placeholders": {"accent": ACCENT, "link": LINK, "topic_accent": TOPIC_ACCENT, "url": SAMPLE_URL},
    }


# ── Widget sample fragments ────────────────────────────────────────────────────────────────────────────────────
# Roadmap C will draw the real widgets with its own primitives; these show the studio roughly what each looks like,
# in the email's style (table layout, inline styles, accent strip), using made-up sample data.

def _widget_card(wid: str, kicker: str, body: str, source: str) -> str:
    from render.palette import CHAR, F

    return (
        f'<!-- WIDGET:{wid} -->\n'
        f'<tr><td class="mob-pad" style="background:#141414;padding:22px 40px 20px;border-left:4px solid {ACCENT};">\n'
        f'<p style="margin:0 0 12px 0;font-family:{F};font-size:11px;font-weight:700;letter-spacing:3px;'
        f'text-transform:uppercase;color:{ACCENT};">&#9670;&nbsp; {kicker}</p>\n'
        f'{body}'
        f'<p style="margin:12px 0 0 0;font-family:{F};font-size:10px;color:{CHAR};letter-spacing:0.5px;">{source}</p>\n'
        f'</td></tr>\n'
        f'<!-- /WIDGET:{wid} -->\n'
    )


def _rows(rows: list[tuple[str, str, str]]) -> str:
    """A small data table: label | value | change. A change starting with ▲/▼ is coloured, and keeps its arrow
    and sign so it reads without colour."""
    return _table("".join(_row(*r) for r in rows))


def _table(rows_html: str) -> str:
    return f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">\n{rows_html}</table>\n'


def _row(label: str, value: str, change: str) -> str:
    from render.palette import ASH, F

    colour = "#5fd08a" if change.startswith("▲") else "#e07a6a" if change.startswith("▼") else "#8a8580"
    return (
        f'<tr><td style="padding:6px 0;border-bottom:1px solid #222222;font-family:{F};font-size:13px;'
        f'color:{ASH};">{label}</td>'
        f'<td align="right" style="padding:6px 0 6px 12px;border-bottom:1px solid #222222;font-family:{F};'
        f'font-size:13px;font-weight:700;color:#faf9f4;white-space:nowrap;">{value}</td>'
        f'<td align="right" width="86" style="padding:6px 0 6px 12px;border-bottom:1px solid #222222;'
        f'font-family:{F};font-size:12px;color:{colour};white-space:nowrap;">{change}</td></tr>\n'
    )


def _lines(items: list[tuple[str, str]]) -> str:
    """A list of (title, detail) lines, title linked to the sample URL."""
    from render.palette import CHAR, F

    return "".join(
        f'<p style="margin:0 0 9px 0;font-family:{F};font-size:14px;line-height:1.45;">'
        f'<a href="https://example.com/sample" target="_blank" rel="noopener noreferrer" '
        f'style="color:#faf9f4;text-decoration:none;font-weight:700;">{t}</a>'
        f'<span style="color:{CHAR};"> &nbsp;&middot;&nbsp; {d}</span></p>\n'
        for t, d in items
    )


def _paper_sample() -> str:
    from render import blocks

    return blocks.research_rows([{
        "title": "Sample: Small Models That Check Their Own Work",
        "authors": ["A. Researcher", "B. Scientist", "C. Engineer", "D. Student"],
        "summary": "A sample hook for the paper of the week: what the paper shows in one sentence, and why a "
                   "non-specialist would find it interesting in a second.",
        "hf_url": "https://huggingface.co/papers",
        "scores": {"total": 36, "upvotes": 214},
    }])


def build_widget_fragments() -> dict:
    from render.palette import ASH, F

    fin = "Sample data &nbsp;&middot;&nbsp; For information only, not financial advice"
    return {
        "paper_spotlight": _paper_sample(),
        "trending_models": _widget_card("trending_models", "Trending on Hugging Face", _rows([
            ("northwind/vision-small-7b", "41.2k", "▲ 12 places"),
            ("helix/coder-mini", "28.9k", "▲ 4 places"),
            ("open-audio/whisperlite", "19.4k", "new"),
            ("tern/reasoner-3b", "12.7k", "▼ 2 places"),
            ("lumen/embed-v2", "9.8k", "▲ 1 place"),
        ]), "Sample data &nbsp;&middot;&nbsp; downloads this week"),
        "market_snapshot": _widget_card("market_snapshot", "The week in markets", _rows([
            ("CAD / USD", "0.7312", "▲ +0.4%"),
            ("US 10-year yield", "4.12%", "▼ −0.06"),
            ("Canada 10-year yield", "3.21%", "▼ −0.03"),
            ("WTI crude", "$71.40", "▲ +2.1%"),
            ("Bitcoin", "$63,250", "▼ −3.2%"),
        ]), fin),
        "rates_board": _widget_card("rates_board", "Central bank rates", _rows([
            ("Federal Reserve", "4.25–4.50%", "next Oct 29"),
            ("Bank of Canada", "2.75%", "next Oct 29"),
            ("European Central Bank", "2.00%", "next Oct 30"),
        ]), fin),
        "week_ahead": _widget_card("week_ahead", "The week ahead", _lines([
            ("Tue &nbsp;US consumer prices", "September CPI, 8:30 ET"),
            ("Wed &nbsp;Bank of Canada", "rate decision, 9:45 ET"),
            ("Thu &nbsp;US GDP", "third quarter, first estimate"),
            ("Fri &nbsp;Canada jobs", "October Labour Force Survey"),
        ]), fin),
        # The mock fills __ROWS__ with one widget-row-up/-down per ticker the reader chose.
        "stock_watchlist": _widget_card("stock_watchlist", "My watchlist", _table("__ROWS__"), fin),
        "row-up": _row("__LABEL__", "__VALUE__", "▲ __CHANGE__"),
        "row-down": _row("__LABEL__", "__VALUE__", "▼ __CHANGE__"),
        "free_games": _widget_card("free_games", "Free this week", _lines([
            ("Lantern Keeper", "Epic Games Store &middot; free until Thu"),
            ("Orbital Gardens", "Steam &middot; free weekend"),
            ("The Quiet Tower", "GOG &middot; free until Sun"),
        ]), "Sample data &nbsp;&middot;&nbsp; check the store before claiming"),
        "most_played": _widget_card("most_played", "Most played on Steam", _rows([
            ("1. Skyline Siege", "1.21M", "peak players"),
            ("2. Harbor Tactics", "684k", "peak players"),
            ("3. Rogue Lanterns", "402k", "peak players"),
            ("4. Neon Drift", "311k", "peak players"),
            ("5. Farmstead Tales", "198k", "peak players"),
        ]), "Sample data &nbsp;&middot;&nbsp; peak concurrent players this week"),
        "deals": _widget_card("deals", "Best deals", _rows([
            ("Ember Cartographer", "$4.99", "−75%"),
            ("Signal Lost", "$7.49", "−70%"),
            ("Tidepool Racing", "$9.99", "−60%"),
            ("Paper Knights", "$12.99", "−50%"),
        ]), "Sample data &nbsp;&middot;&nbsp; prices in USD"),
        "out_this_week": _widget_card("out_this_week", "Out this week", _lines([
            ("Hollow Meridian", "PC, PlayStation 5 &middot; Tue"),
            ("Kart Club Deluxe", "Switch &middot; Thu"),
            ("Frontier Outpost 2", "PC, Xbox &middot; Fri"),
        ]), "Sample data"),
        "crowdfunding_watch": _widget_card("crowdfunding_watch", "On Kickstarter &amp; Gamefound", _rows([
            ("Lighthouse Keepers", "Kickstarter", "312% funded"),
            ("Dune Caravans", "Gamefound", "88% · 9 days left"),
            ("Pocket Dungeons", "Kickstarter", "starts Thu"),
        ]), "Sample data &nbsp;&middot;&nbsp; only what the week's stories say"),
        "release_tracker": _widget_card("release_tracker", "New versions", _rows([
            ("PrusaSlicer", "2.9.1", "new this week"),
            ("OrcaSlicer", "2.3.0", "new this week"),
            ("Klipper", "v0.13.1", ""),
            ("Marlin", "2.1.3", ""),
        ]), "Sample data &nbsp;&middot;&nbsp; from each project's GitHub releases"),
        "model_of_week": _widget_card("model_of_week", "Model of the week", _lines([
            ("Print-in-place articulated heron", "by a sample designer &middot; 14 h, no supports"),
        ]), "Sample data"),
        "number_of_week": _widget_card("number_of_week", "Number of the week", (
            f'<p style="margin:0 0 6px 0;font-family:{F};font-size:40px;font-weight:700;line-height:1;'
            f'color:{ACCENT};">41%</p>\n'
            f'<p style="margin:0;font-family:{F};font-size:14px;line-height:1.6;color:{ASH};">A sample sentence '
            f'from one of the week&#39;s stories, with the number in it and where it came from.</p>\n'
        ), "Sample data &nbsp;&middot;&nbsp; from this week's stories"),
    }


def build_fragments() -> dict:
    """{file name: html}: the real renderer's pieces with placeholders in place of the values."""
    from render import blocks, shell

    inner = (
        shell.masthead("__NAME__", "__TAGLINE__", "__WEEK__", ACCENT, "", "__TOC1__", "__TOC2__")
        + "\n\n    __INTRO__"
        + "\n\n    __LEAD__"
        + "\n    __REST__"
        + "\n\n    " + shell.footer("__NAME__", ACCENT, "", "__DISCLAIMERS__")
    )
    card = {"title": "__TITLE__", "url": SAMPLE_URL, "summary": "__SUMMARY__"}
    hn_card = dict(card, hn_score="__HN__")
    frags = {
        "page.html": shell.page("__NAME__", "__PREHEADER__", inner),
        "intro.html": shell.intro_row("__INTRO__", "__WEEK__", "__LABEL__"),
        "toc-cell.html": blocks.toc_cell("__NUM__", "__LABEL__", ACCENT),
        "section.html": blocks.section_rows("__NAME__", "__NUM__", "__ICON__", "__BODY__", ACCENT,
                                            marker="__MARKER__"),
        "section-mixed.html": blocks.section_rows("__NAME__", "__NUM__", "__ICON__", "__BODY__", ACCENT,
                                                  marker="__MARKER__", topic_label="__TOPIC__",
                                                  topic_accent=TOPIC_ACCENT),
        "card.html": blocks.render_article_card(card, False, ACCENT, LINK),
        "card-last.html": blocks.render_article_card(card, True, ACCENT, LINK),
        "card-hn.html": blocks.render_article_card(hn_card, False, ACCENT, LINK),
        "card-hn-last.html": blocks.render_article_card(hn_card, True, ACCENT, LINK),
        "empty.html": blocks.empty_note("__TEXT__"),
        "share.html": blocks.share_strip(),
        "disclaimer.html": shell.disclaimer_html("__TEXT__"),
    }
    for wid, html in build_widget_fragments().items():
        frags[f"widget-{wid}.html"] = html
    return frags


def _dump_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    os.makedirs(FRAGMENT_DIR, exist_ok=True)
    with open(CATALOG_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(_dump_json(build_catalog()))
    with open(MOCK_CONTENT_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(_dump_json(build_mock_content()))
    for name, html in build_fragments().items():
        with open(os.path.join(FRAGMENT_DIR, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
    print(f"wrote {CATALOG_PATH}, {MOCK_CONTENT_PATH} and {len(build_fragments())} fragments")


if __name__ == "__main__":
    main()
