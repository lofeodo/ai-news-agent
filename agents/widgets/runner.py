# agents/widgets/runner.py
#
# Produce a run's edition widgets, bounded: each produce() gets WIDGET_TIMEOUT seconds, all of them share
# EDITION_BUDGET seconds in a small thread pool, data must be JSON and at most MAX_DATA_BYTES, and the rendered
# fragment at most MAX_FRAGMENT_BYTES. A widget that breaks any rule is left out with a status; nothing here
# raises, so a widget can never fail a run.

import json
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from . import registry
from .base import ProduceContext, WidgetSkip
from .config import ConfigError, validate

WIDGET_TIMEOUT = 20.0
EDITION_BUDGET = 90.0
MAX_WORKERS = 4
MAX_DATA_BYTES = 20_000
MAX_FRAGMENT_BYTES = 8_000

OK, FAILED, SKIPPED_NO_KEY, TIMED_OUT, EMPTY, STALE = "ok", "failed", "skipped_no_key", "timed_out", "empty", "stale"
STATUSES = (OK, FAILED, SKIPPED_NO_KEY, TIMED_OUT, EMPTY, STALE)


@dataclass
class RunResult:
    data: dict = field(default_factory=dict)      # {widget id: data} for status ok only
    status: dict = field(default_factory=dict)    # {widget id: {"status", "error"?, "seconds"?}}
    configs: dict = field(default_factory=dict)   # {widget id: normalized config}

    def summary(self) -> dict:
        """Statuses only, small enough for the run doc (widgets_summary)."""
        return {wid: s["status"] for wid, s in self.status.items()}


def _entry(e) -> tuple[str, dict]:
    if isinstance(e, dict):
        return e.get("id"), e.get("config") or {}
    return getattr(e, "id", None), dict(getattr(e, "config", None) or {})


def _sample_look():
    from render import Look
    from render.palette import AI_LINK, AMBER
    return Look(name="", tagline="", accent=AMBER, link_color=AI_LINK, week_of="")


def check_output(widget, data, config) -> str | None:
    """Why this data must not be shown, or None: not JSON, too big, render raises, or the fragment is too big."""
    try:
        blob = json.dumps(data, allow_nan=False, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        return f"data is not JSON: {e}"
    if len(blob.encode("utf-8")) > MAX_DATA_BYTES:
        return f"data is {len(blob.encode('utf-8')):,} bytes (max {MAX_DATA_BYTES:,})"
    try:
        html = widget.render(data, config, _sample_look())
    except Exception as e:  # render must never raise; if it does, leave the widget out
        return f"render failed: {type(e).__name__}: {e}"
    size = len((html or "").encode("utf-8"))
    if size > MAX_FRAGMENT_BYTES:
        return f"fragment is {size:,} bytes (max {MAX_FRAGMENT_BYTES:,})"
    if not html:
        return "render returned nothing"
    return None


def produce_one(widget, ctx: ProduceContext, config: dict) -> tuple[str, dict | None, str | None]:
    """(status, data, error) for one widget, run in the caller's thread (no timeout here)."""
    try:
        data = widget.produce(ctx, config)
    except WidgetSkip as e:
        return EMPTY, None, str(e) or None
    except Exception as e:
        return FAILED, None, f"{type(e).__name__}: {str(e)[:200]}"
    if not data:
        return EMPTY, None, None
    if widget.is_stale(data, ctx.now):
        return STALE, None, f"data as of {data.get('as_of')} is older than {widget.max_age_hours}h"
    problem = check_output(widget, data, config)
    if problem:
        return FAILED, None, problem
    return OK, data, None


def produce_edition_widgets(ctx: ProduceContext, entries, timeout: float = WIDGET_TIMEOUT,
                            budget: float = EDITION_BUDGET, max_workers: int = MAX_WORKERS) -> RunResult:
    result = RunResult()
    jobs = []
    for e in entries or []:
        wid, raw_config = _entry(e)
        widget = registry.get(wid)
        if widget is None:
            result.status[wid] = {"status": FAILED, "error": "unknown widget"}
            continue
        if widget.scope != "edition":
            result.status[wid] = {"status": FAILED, "error": "not an edition widget"}
            continue
        missing = widget.missing_keys()
        if missing:
            result.status[wid] = {"status": SKIPPED_NO_KEY, "error": f"no key ({', '.join(missing)})"}
            continue
        try:
            config = validate(widget.config_fields, raw_config)
        except ConfigError as err:
            result.status[wid] = {"status": FAILED, "error": f"bad config: {err}"}
            continue
        result.configs[wid] = config
        jobs.append((wid, widget, config))
    if not jobs:
        return result

    started: dict[str, float] = {}

    def call(wid, widget, config):
        started[wid] = time.monotonic()
        return produce_one(widget, ctx, config)

    t0 = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="widget")
    pending = {pool.submit(call, wid, w, c): wid for wid, w, c in jobs}
    try:
        while pending:
            now = time.monotonic()
            if now - t0 >= budget:
                break
            done, _ = wait(pending, timeout=min(0.05, budget - (now - t0)), return_when=FIRST_COMPLETED)
            for fut in done:
                wid = pending.pop(fut)
                status, data, error = fut.result()
                entry = {"status": status, "seconds": round(time.monotonic() - started.get(wid, t0), 2)}
                if error:
                    entry["error"] = error
                result.status[wid] = entry
                if status == OK:
                    result.data[wid] = data
            now = time.monotonic()
            for fut, wid in list(pending.items()):
                if wid in started and now - started[wid] >= timeout:
                    pending.pop(fut)
                    result.status[wid] = {"status": TIMED_OUT, "error": f"no answer within {timeout:g}s"}
        for fut, wid in pending.items():
            fut.cancel()
            result.status[wid] = {"status": TIMED_OUT, "error": f"edition budget of {budget:g}s used up"}
    finally:
        # A thread stuck in produce() keeps running in the background; its HTTP calls carry their own timeouts.
        pool.shutdown(wait=False, cancel_futures=True)
    for wid, _, _ in jobs:  # keep profile order in the status dict
        result.status[wid] = result.status.pop(wid)
    return result
