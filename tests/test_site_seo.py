"""Static checks that keep the site findable by search engines (no network)."""
import json
import re
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "public" / "newsletter"
ORIGIN = "https://newsletter.lofeodo.com"

INDEXED = {"index.html": "/", "issues.html": "/issues.html", "newsletters.html": "/newsletters.html"}
NOINDEX = ["login.html", "auth-callback.html", "preferences.html", "sections.html",
           "unsubscribe.html", "welcome.html", "share.html", "studio.html", "widgets.html"]


def _read(name: str) -> str:
    return (SITE / name).read_text(encoding="utf-8")


def test_robots_allows_crawling_and_points_to_the_sitemap():
    robots = _read("robots.txt")
    assert "Disallow: /\n" not in robots
    assert f"Sitemap: {ORIGIN}/sitemap.xml" in robots


def test_sitemap_lists_every_indexed_page():
    locs = re.findall(r"<loc>([^<]+)</loc>", _read("sitemap.xml"))
    assert sorted(locs) == sorted(ORIGIN + path for path in INDEXED.values())


def test_indexed_pages_have_a_canonical_on_the_custom_domain():
    # The site is also served on latentspacemail.web.app; the canonical tells search engines
    # which address is the real one.
    for name, path in INDEXED.items():
        src = _read(name)
        assert f'<link rel="canonical" href="{ORIGIN}{path}">' in src, name
        assert 'name="description"' in src, name
        assert 'name="robots"' not in src, name


def test_account_and_utility_pages_are_noindex():
    for name in NOINDEX:
        assert re.search(r'<meta name="robots" content="noindex', _read(name)), name


def test_homepage_names_the_site_for_search():
    src = _read("index.html")
    assert re.search(r"<title>Latent SpaceMail[^<]*newsletter", src)
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', src, re.DOTALL)
    data = json.loads(block.group(1))
    assert data["name"] == "Latent SpaceMail" and data["url"] == ORIGIN + "/"
