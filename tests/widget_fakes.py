"""Test helpers for widget providers: a fake HTTP session that answers from tests/fixtures/widgets/, so no test
touches the network, and a context builder."""
import json
import os
from datetime import datetime, timezone

import requests

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "widgets")
NOW = datetime(2026, 10, 18, 16, 0, tzinfo=timezone.utc)   # the Sunday draft run after the recordings


def fixture_text(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def fixture_json(name: str):
    return json.loads(fixture_text(name))


class FakeResponse:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")
        self.content = self.text.encode("utf-8")

    def json(self):
        if self._body is None:
            return json.loads(self.text)
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


class FakeSession:
    """routes: {url substring: FakeResponse | callable(url, kwargs) -> FakeResponse | Exception}. The first
    matching substring wins; an unmatched URL raises, so a test notices an unexpected call."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls = []
        self.headers = {}

    def get(self, url, **kw):
        self.calls.append((url, kw))
        for key, resp in self.routes.items():
            if key in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp(url, kw) if callable(resp) else resp
        raise AssertionError(f"unexpected URL {url}")


def _steam_app(url, kw):
    appid = url.split("appids=")[1].split("&")[0]
    return FakeResponse(200, {appid: fixture_json("steam_appdetails.json").get(appid, {"success": False})})


def all_fixture_routes() -> dict:
    """Every recorded source, so any keyless widget can be produced end to end."""
    routes = {f"/repos/{repo}/releases/latest": FakeResponse(r["status"], r["body"])
              for repo, r in fixture_json("github_releases_latest.json").items()}
    routes.update({
        "freeGamesPromotions": FakeResponse(200, fixture_json("epic_free_games.json")),
        "gamerpower.com": FakeResponse(200, fixture_json("gamerpower.json")),
        "GetMostPlayedGames": FakeResponse(200, fixture_json("steam_most_played.json")),
        "appdetails": _steam_app,
        "cheapshark.com/api/1.0/deals": FakeResponse(200, fixture_json("cheapshark_deals.json")),
        "cheapshark.com/api/1.0/stores": FakeResponse(200, fixture_json("cheapshark_stores.json")),
        "huggingface.co/api/models": FakeResponse(200, fixture_json("hf_trending.json")),
        "valet/observations/V39079": FakeResponse(200, fixture_json("boc_policy.json")),
        "bankofcanada.ca/valet": FakeResponse(200, fixture_json("boc_market.json")),
        "home.treasury.gov": FakeResponse(200, text=fixture_text("treasury_yields.csv")),
        "markets.newyorkfed.org": FakeResponse(200, fixture_json("nyfed_effr.json")),
        "data-api.ecb.europa.eu": FakeResponse(200, fixture_json("ecb_dfr.json")),
    })
    return routes


def ctx(routes: dict, **kw):
    from widgets import ProduceContext
    kw.setdefault("now", NOW)
    kw.setdefault("week", "2026-10-19")
    return ProduceContext(http=FakeSession(routes), **kw)
