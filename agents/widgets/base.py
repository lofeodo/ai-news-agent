# agents/widgets/base.py
#
# The widget interface (contract C4 in docs/plans/newsletter-platform.md). A widget is a small data block with
# its own source: `produce` fetches (network allowed, bounded by the runner), `render` draws an email-safe HTML
# fragment from that data (no network, never raises), `sample_data` feeds the admin gallery, the studio and tests.
# How to add one: docs/widgets.md.

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

USER_AGENT = "LatentSpaceMail-widgets/1.0 (+https://newsletter.lofeodo.com)"
REQUEST_TIMEOUT = 10   # seconds per HTTP call; the runner's per-widget timeout bounds the whole produce()

SCOPES = ("edition", "personal")
KINDS = ("external", "derived", "pipeline")


class WidgetSkip(Exception):
    """Raised by produce() when there is nothing to show this week (not an error: the widget is left out)."""


def make_session() -> requests.Session:
    """HTTP session with a descriptive User-Agent. requests reads HTTPS_PROXY / HTTP_PROXY from the environment,
    so on Cloud Run the calls go through the same Squid proxy as the other fetchers."""
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


@dataclass
class ProduceContext:
    """What produce() may use. `week` is the send date (YYYY-MM-DD); `picks` maps section name -> the edition's
    picked articles (derived widgets); `run_doc` is the pipeline run doc (pipeline widgets); `client` is an
    Anthropic-style client or None; `usage` collects one dict per model call."""
    topic: str = "ai"
    run_id: str = ""
    week: str = ""
    picks: dict = field(default_factory=dict)
    run_doc: dict = field(default_factory=dict)
    http: requests.Session | None = None
    client: object = None
    usage: list = field(default_factory=list)
    now: datetime | None = None

    def __post_init__(self):
        if self.http is None:
            self.http = make_session()
        if self.now is None:
            self.now = datetime.now(timezone.utc)

    def get(self, url: str, **kw) -> requests.Response:
        kw.setdefault("timeout", REQUEST_TIMEOUT)
        r = self.http.get(url, **kw)
        r.raise_for_status()
        return r

    def get_json(self, url: str, **kw):
        return self.get(url, **kw).json()

    def log_usage(self, widget_id: str, response) -> None:
        u = getattr(response, "usage", None)
        self.usage.append({
            "widget": widget_id,
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
        })


class Widget:
    """Subclass, set the class attributes, implement produce/render/sample_data, and decorate with
    @registry.register."""
    id: str = ""
    name: str = ""
    description: str = ""
    topics: tuple = ("*",)          # topic ids it belongs to, or ("*",) for any
    scope: str = "edition"          # "edition" (once per topic run) or "personal" (per config, at send time)
    kind: str = "external"          # external data, derived from the edition (one Haiku call), or pipeline
    premium: bool = False           # personal widgets are always premium
    commercial_use: bool = True     # False when the source's terms forbid commercial use: free ready-made only
    config_fields: tuple = ()       # ({"name", "type", "label", "options"?, "min"?, "max"?, "default"}, ...)
    keys: tuple = ()                # env vars the source needs; absent = skipped_no_key (ships disabled)
    max_age_hours: float | None = None   # data whose `as_of` is older than this is never shown as current
    timeout: float | None = None    # seconds for produce(); None = the runner's default (20); still within the budget
    toc: tuple | None = None        # (short, label) for the table of contents, or None

    def produce(self, ctx: ProduceContext, config: dict) -> dict:
        raise NotImplementedError

    def render(self, data: dict, config: dict, look) -> str:
        raise NotImplementedError

    def sample_data(self) -> dict:
        raise NotImplementedError

    # helpers the runner uses

    def missing_keys(self) -> list[str]:
        return [k for k in self.keys if not (os.environ.get(k) or "").strip()]

    def is_stale(self, data: dict, now: datetime) -> bool:
        """True when data['as_of'] (ISO date or datetime) is older than max_age_hours."""
        if not self.max_age_hours or not isinstance(data, dict) or not data.get("as_of"):
            return False
        try:
            as_of = datetime.fromisoformat(str(data["as_of"]).replace("Z", "+00:00"))
        except ValueError:
            return True
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        return now - as_of > timedelta(hours=self.max_age_hours)

    def info(self) -> dict:
        """The metadata the studio catalog shows (C6 `widgets` entries)."""
        return {"id": self.id, "name": self.name, "description": self.description, "topics": list(self.topics),
                "scope": self.scope, "premium": self.premium or self.scope == "personal",
                "config_fields": [dict(f) for f in self.config_fields]}
