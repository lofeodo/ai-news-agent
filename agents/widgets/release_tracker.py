# agents/widgets/release_tracker.py
#
# New versions (3D printing): the latest stable release of the main slicers and printer firmwares, from the
# GitHub Releases API (/releases/latest already skips drafts and pre-releases). Unauthenticated: 60 calls an hour
# per IP, and this makes one per repo. A repo with no releases or an error is left out; the widget only fails
# when every repo does.

import re
from datetime import datetime, timedelta, timezone

import requests

from . import ui
from .base import Widget, WidgetSkip
from .registry import register

REPOS = (
    ("prusa3d/PrusaSlicer", "PrusaSlicer"),
    ("SoftFever/OrcaSlicer", "OrcaSlicer"),
    ("bambulab/BambuStudio", "Bambu Studio"),
    ("Ultimaker/Cura", "UltiMaker Cura"),
    ("CrealityOfficial/CrealityPrint", "Creality Print"),
    ("MarlinFirmware/Marlin", "Marlin firmware"),
    ("prusa3d/Prusa-Firmware-Buddy", "Prusa firmware (Buddy)"),
)
NEW_DAYS = 7
_PREFIX = re.compile(r"^(version[_-]?|v)(?=\d)", re.IGNORECASE)


def clean_version(tag) -> str:
    return _PREFIX.sub("", str(tag or "").strip())[:24]


def _parse_time(value):
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


@register
class ReleaseTracker(Widget):
    id = "release_tracker"
    name = "New versions"
    description = "The latest release of the popular slicers and printer firmwares, with what is new this week."
    topics = ("printing3d",)

    def produce(self, ctx, config):
        rows, errors = [], 0
        for repo, label in REPOS:
            try:
                r = ctx.get(f"https://api.github.com/repos/{repo}/releases/latest",
                            headers={"Accept": "application/vnd.github+json"})
                rel = r.json()
            except requests.HTTPError as e:
                if getattr(e.response, "status_code", None) != 404:  # 404 = the repo publishes no releases
                    errors += 1
                continue
            except Exception:
                errors += 1
                continue
            if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
                continue
            published = _parse_time(rel.get("published_at"))
            version = clean_version(rel.get("tag_name"))
            if not version or published is None:
                continue
            rows.append({"name": label, "version": version, "date": published.date().isoformat(),
                         "url": rel.get("html_url"), "new": ctx.now - published <= timedelta(days=NEW_DAYS)})
        if not rows:
            raise WidgetSkip("no release found") if not errors else RuntimeError(f"{errors} repo lookups failed")
        rows.sort(key=lambda r: r["date"], reverse=True)
        return {"rows": rows, "as_of": ctx.now.isoformat()}

    def render(self, data, config, look):
        rows = [r for r in (data or {}).get("rows") or [] if isinstance(r, dict)]
        if not rows:
            return ""
        new = sum(1 for r in rows if r.get("new"))
        table = ui.data_table(
            ["Software", "Version", "Released"],
            [[ui.Raw(ui.link(r.get("name"), r.get("url")) + (ui.badge("NEW", ui.UP) if r.get("new") else "")),
              r.get("version"), ui.fmt_date(r.get("date"))] for r in rows],
            numeric=(1, 2))
        aside = f"{new} new this week" if new else "nothing new this week"
        return ui.card(self.id, self.name, table, ui.accent_of(look), "🔧", aside,
                       ui.attribution([("GitHub releases", "https://github.com")], data.get("as_of")))

    def sample_data(self):
        return {"as_of": "2026-10-18T16:00:00+00:00", "rows": [
            {"name": "Marlin firmware", "version": "2.1.2.9", "date": "2026-10-12",
             "url": "https://github.com/MarlinFirmware/Marlin/releases", "new": True},
            {"name": "Prusa firmware (Buddy)", "version": "6.9.2", "date": "2026-10-07",
             "url": "https://github.com/prusa3d/Prusa-Firmware-Buddy/releases", "new": False},
            {"name": "Creality Print", "version": "7.3.0", "date": "2026-09-24",
             "url": "https://github.com/CrealityOfficial/CrealityPrint/releases", "new": False},
        ]}
