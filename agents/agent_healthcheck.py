# agents/agent_healthcheck.py
#
# Standalone check, independent of the fetch->summarize->compose->send chain.
# Two separate checks, each triggered by its own Cloud Scheduler job (POST to
# "/?check=draft" or "/?check=send") rather than by Pub/Sub:
#   draft  Sunday ~1:15 PM  did the newsletter COMPOSE properly? (all stages up
#                           to agent3, plus the composed output itself)
#   send   Monday 7:10 AM   did the newsletter SEND? (every stage, agent4's
#                           send summary, reader clicks)
# With no `check` (manual run, or the old single job) it picks by weekday:
# Sunday -> draft, otherwise send. It has no run_id for the
# pipeline run it's checking — it looks that up itself, by most recent
# started_at in the pipeline_runs collection.
#
# Sends exactly one email to ALERT_EMAIL every run, whether the pipeline
# looks healthy or not -- a weekly heartbeat, not just a failure alert.
# Never touches the subscribers collection or the subscriber send path. The
# only email this can send goes to ALERT_EMAIL, via the same send_email()
# helper agent4 uses for real sends — reused for the SendGrid call only.

import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import pricing
from config import GCP_PROJECT_ID, FIRESTORE_COLLECTION, USE_FIRESTORE, ALERT_EMAIL, parse_started_at

import agent4_send  # reuse send_email() / _get_sendgrid_api_key() only

NEWSLETTER_NAME = "Latent SpaceMail"

# How stale the latest pipeline_runs doc can be before we treat it as "no
# run happened this week" rather than evaluating its (old) completion state.
# The pipeline starts Sunday 12:00 and the Monday 7:10 check must still accept
# it (~19h old); a prior week's run is 7 days old, so 30h still catches it.
STALE_AFTER_HOURS = 30
# The draft check runs ~1h after the pipeline starts, so a much older run is stale.
DRAFT_STALE_AFTER_HOURS = 4

CHECK_MODES = ("draft", "send")

# Every subscriber-preference variant agent3 composes (keys of agent3's NEWSLETTER_VARIANTS),
# and the smallest a real rendered variant plausibly is (real ones are ~100 KB).
VARIANT_KEYS = ("0_0", "1_0", "0_1", "1_1")
MIN_VARIANT_CHARS = 5000
PLACEHOLDERS = ("{{UNSUBSCRIBE_URL}}", "{{PREFERENCES_URL}}")

# (Firestore field, human label) — checked in pipeline order.
EXPECTED_STAGES = [
    ("scored_papers",        "agent1a (fetch + score papers)"),
    ("news_filtered",        "agent1b (fetch + filter news)"),
    ("paper_summaries",      "agent2a (summarize papers)"),
    ("news_summaries",       "agent2b (summarize news)"),
    ("newsletter_composed",  "agent3 (compose newsletter)"),
    ("agent4_send_summary",  "agent4 (send newsletter)"),
]

ERROR_AGENTS = ["agent1a", "agent1b", "agent2a", "agent2b", "agent3", "agent4"]


def _latest_run_doc(db):
    """Return (run_id, doc dict) for the most recently started pipeline run, or (None, None)."""
    from google.cloud import firestore

    docs = (
        db.collection(FIRESTORE_COLLECTION)
        .order_by("started_at", direction=firestore.Query.DESCENDING)
        .limit(1)
        .stream()
    )
    for d in docs:
        return d.id, (d.to_dict() or {})
    return None, None


def _resolve_mode(check: str | None, now: datetime | None = None) -> str:
    """'draft' or 'send'. An explicit `check` wins; otherwise Sunday (Toronto) is a draft check."""
    if check:
        if check not in CHECK_MODES:
            raise ValueError(f"unknown check {check!r}; expected one of {CHECK_MODES}")
        return check
    sunday = (now or datetime.now(timezone.utc)).astimezone(config.NEWSLETTER_TZ).weekday() == 6
    return "draft" if sunday else "send"


def _check_composition(doc: dict) -> list[str]:
    """Problems with the composed newsletter itself (draft check): all four variants present and
    non-trivial, still carrying agent4's per-subscriber placeholders, and dated for the send day."""
    problems = []
    variants = doc.get("newsletter_variants") or {}
    for key in VARIANT_KEYS:
        html = variants.get(key)
        if not html:
            problems.append(f"newsletter variant {key} is missing")
            continue
        if len(html) < MIN_VARIANT_CHARS:
            problems.append(f"newsletter variant {key} is only {len(html)} chars (expected at least {MIN_VARIANT_CHARS})")
        for ph in PLACEHOLDERS:
            if ph not in html:
                problems.append(f"newsletter variant {key} has no {ph} placeholder, so agent4 cannot personalize it")

    subject = doc.get("newsletter_subject") or ""
    started_raw = doc.get("started_at")
    if not subject:
        problems.append("newsletter_subject is missing")
    elif started_raw:
        expected = config.newsletter_send_date(parse_started_at(started_raw)).strftime("%B %d, %Y")
        if not subject.endswith(expected):
            problems.append(f"newsletter subject {subject!r} is not dated for the send day ({expected})")
    return problems


def _diagnose(doc: dict, mode: str = "send") -> list[str]:
    """Return human-readable problem descriptions for one pipeline_runs doc. Empty means healthy.
    In 'draft' mode the send stage is not expected yet and the composed output is checked instead."""
    draft_only = mode == "draft"
    problems = []

    for agent in ERROR_AGENTS:
        error = doc.get(f"{agent}_error")
        if error:
            failed_at = doc.get(f"{agent}_failed_at", "unknown time")
            problems.append(f"{agent} failed at {failed_at}: {error}")

    for field, label in EXPECTED_STAGES:
        if draft_only and field == "agent4_send_summary":
            continue
        if not doc.get(field):
            problems.append(f"{label} never completed — '{field}' missing from pipeline_runs")

    if draft_only:
        if doc.get("newsletter_composed"):
            problems += _check_composition(doc)
        return problems

    send_summary = doc.get("agent4_send_summary")
    if send_summary:
        total  = send_summary.get("total", 0)
        sent   = send_summary.get("sent", 0)
        failed = send_summary.get("failed", 0)
        if total and sent == 0:
            problems.append(f"agent4 ran but sent 0/{total} emails (failed={failed})")
        elif failed:
            problems.append(f"agent4 had {failed}/{total} failed sends (informational, not necessarily a full failure)")

    return problems


def _format_drift(result: dict) -> str:
    if result["status"] == "insufficient_history":
        return (f"Drift check: not enough history yet ({result['baseline_runs']} usable prior runs, "
                f"need {config.DRIFT_MIN_BASELINE_RUNS}).")
    lines = [f"Drift check vs prior {result['baseline_runs']} runs: "
             f"{'DRIFT FLAGGED' if result['status'] == 'drift' else 'no drift'}"]
    for r in result["results"]:
        flag = {"drift": "DRIFT", "ok": "ok", "insufficient_data": "n/a"}[r["status"]]
        if r["status"] == "insufficient_data":
            detail = f"n={r['n_current']} vs {r['n_baseline']}"
        elif r["metric"] == "confidence":
            detail = f"mean {r['mean_current']:.2f} vs {r['mean_baseline']:.2f}, KS D={r['statistic']:.2f}, p={r['p_value']:.3g}"
        elif r["metric"] == "category_mix":
            detail = (f"largest shift {r['largest_shift_category']} {r['largest_shift']:+.1%}, "
                      f"{r['method']} p={r['p_value']:.3g}")
        else:
            detail = f"{r['rate_current']:.1%} vs {r['rate_baseline']:.1%}, p={r['p_value']:.3g}"
        lines.append(f"  - {r['metric']}: {flag} ({detail})")
    lines.append("  (Informational: a drift flag never marks the pipeline as failed. Small weekly samples; "
                 "a flag means look, not broken.)")
    return "\n".join(lines)


def _drift_section(db, run_id: str, doc: dict) -> str:
    """Drift report text for the email. Never raises: a monitoring failure must not suppress
    the heartbeat or change the pipeline's health status."""
    try:
        import drift
        import drift_history

        current = drift_history.run_summary(db, run_id, doc)
        if current is None:
            return "Drift check: skipped (this run has no graph-mode agent1b summary)."
        baseline = drift_history.load_baseline(db, run_id, config.DRIFT_BASELINE_RUNS)
        result = drift.evaluate(
            current, baseline,
            min_runs=config.DRIFT_MIN_BASELINE_RUNS, p_threshold=config.DRIFT_P_THRESHOLD,
            min_ks_d=config.DRIFT_KS_MIN_D, min_share_shift=config.DRIFT_MIN_SHARE_SHIFT,
            min_rate_shift=config.DRIFT_MIN_RATE_SHIFT,
        )
        return _format_drift(result)
    except Exception as e:
        print(f"[healthcheck]  drift check failed (ignored): {e}", flush=True)
        return f"Drift check: failed ({type(e).__name__}: {e}). This does not affect the pipeline status above."


def _usd(x) -> str:
    return f"${x:,.2f}" if x is not None else "n/a"


def _format_usage(cur: dict, result: dict) -> str:
    ls = _usd(cur["langsmith_cost"]) if cur["langsmith_cost"] is not None else "n/a (not every call priced)"
    lines = [
        f"LLM usage this run: {cur['tokens']:,} tokens ({cur['input']:,} in / {cur['output']:,} out, {cur['calls']} calls)",
        f"  cost: LangSmith {ls} | list-price estimate {_usd(cur['table_cost'])} (Haiku list price as of {pricing.PRICES_AS_OF})",
    ]
    lines += [f"  - {name}: {a['tokens']:,} tokens (~{_usd(a['table_cost'])})" for name, a in sorted(cur["agents"].items())]
    if result["status"] == "insufficient_history":
        lines.append(f"Usage drift: not enough history yet ({result['baseline_runs']} archived prior runs, "
                     f"need {config.DRIFT_MIN_BASELINE_RUNS}).")
        return "\n".join(lines)
    lines.append(f"Usage drift vs prior {result['baseline_runs']} runs (median): "
                 f"{'DRIFT FLAGGED' if result['status'] == 'drift' else 'no drift'}")
    for r in result["results"]:
        if r["status"] != "ok" and r["status"] != "drift":
            continue
        ratio = f"{r['ratio']:+.0%}" if r["ratio"] is not None else "n/a"
        fmt = _usd if "cost" in r["metric"] else (lambda v: f"{v:,.0f}")
        lines.append(f"  - {r['metric']}: {'DRIFT' if r['status'] == 'drift' else 'ok'} "
                     f"({fmt(r['current'])} vs median {fmt(r['median'])}, {ratio})")
    lines.append("  (Informational: a flag never marks the pipeline as failed.)")
    return "\n".join(lines)


def _usage_section(db, run_id: str, doc: dict) -> str:
    """Token and cost report. Archives this run's LangSmith usage to Firestore first (LangSmith
    only keeps 14 days), then compares with the archived prior runs. Never raises, never changes
    the pipeline's health status, never suppresses the heartbeat."""
    try:
        import drift
        import drift_history
        import usage_archive

        if not os.environ.get("LANGSMITH_API_KEY"):
            return "Usage check: skipped (LANGSMITH_API_KEY is not set on the healthcheck service)."
        from langsmith import Client

        ls = Client()
        prior = usage_archive.catch_up(db, ls, drift_history.recent_runs(db, run_id, config.DRIFT_BASELINE_RUNS))
        current_raw = usage_archive.archive_run(db, ls, run_id, doc)
        if current_raw is None:
            return ("Usage check: no traced Claude calls found in LangSmith for this run "
                    "(tracing may not be enabled on every agent yet).")
        current = drift.usage_totals(current_raw)
        result = drift.evaluate_usage(
            current, [drift.usage_totals(u) for u in prior.values() if u],
            min_runs=config.DRIFT_MIN_BASELINE_RUNS, min_ratio=config.DRIFT_USAGE_MIN_RATIO,
            min_tokens=config.DRIFT_USAGE_MIN_TOKENS, min_usd=config.DRIFT_USAGE_MIN_USD,
        )
        return _format_usage(current, result)
    except Exception as e:
        print(f"[healthcheck]  usage check failed (ignored): {e}", flush=True)
        return f"Usage check: failed ({type(e).__name__}: {e}). This does not affect the pipeline status above."


def _format_judge(res: dict, result: dict | None) -> str:
    lines = [f"Summary faithfulness (judge {res.get('model')}, estimated ${res.get('estimated_usd', 0):.2f}, "
             f"spent ${res.get('cost_usd', 0):.2f}):"]
    if res.get("skipped") == "cost_cap":
        lines.append(f"  - skipped: estimated cost ${res.get('estimated_usd', 0):.2f} is over the ${res.get('cap_usd', 0):.2f} weekly cap.")
        return "\n".join(lines)
    n = res.get("judged", 0)
    if n == 0:
        lines.append(f"  - nothing judged ({res.get('no_source', 0)} without stored source, {res.get('errors', 0)} errors).")
        return "\n".join(lines)
    lines.append(f"  - unsupported: {res['unsupported']}/{n} = {res['unsupported'] / n:.0%} "
                 f"(95% CI {res['unsupported_ci_low']:.0%}-{res['unsupported_ci_high']:.0%}; "
                 f"{res.get('no_source', 0)} no source, {res.get('errors', 0)} errors)")
    if result is None or result["status"] == "insufficient_history":
        lines.append(f"  - drift: not enough history yet ({(result or {}).get('baseline_runs', 0)} prior judged runs).")
    else:
        r = result["results"][0]
        verdict = "DRIFT" if result["status"] == "drift" else "no drift"
        if r["status"] == "insufficient_data":
            verdict = "not enough data"
        else:
            verdict += f" ({r['rate_current']:.0%} vs {r['rate_baseline']:.0%} over {result['baseline_runs']} prior runs)"
        lines.append(f"  - drift: {verdict}")
    mode = "alerting on" if config.JUDGE_ALERTING_ENABLED else "informational until calibrated against human labels"
    lines.append(f"  ({mode}. Small weekly sample: a flag means look, not broken. Same-family judge.)")
    return "\n".join(lines)


def _judge_section(db, run_id: str, doc: dict) -> tuple[str, bool]:
    """Weekly summary-faithfulness report. Returns (text, flagged). Never raises: a failure here
    must not suppress the heartbeat or change the pipeline's health status."""
    try:
        import drift
        import drift_history
        import online_judge
        import tracing

        if not os.environ.get("ANTHROPIC_1ST_API_KEY"):
            return "Summary faithfulness: skipped (ANTHROPIC_1ST_API_KEY is not set on the healthcheck service).", False
        res = online_judge.judge_run(db, tracing.make_client("judge", run_id), run_id, doc)
        prior = [(d or {}).get("judge_results") or {} for _, d in
                 drift_history.recent_runs(db, run_id, config.DRIFT_BASELINE_RUNS)]
        result = None
        if res.get("judged"):
            result = drift.evaluate_judge(res, prior, min_runs=config.DRIFT_MIN_BASELINE_RUNS,
                                          p_threshold=config.DRIFT_P_THRESHOLD,
                                          min_shift=config.JUDGE_MIN_UNSUPPORTED_SHIFT)
        return _format_judge(res, result), bool(result and result["status"] == "drift")
    except Exception as e:
        print(f"[healthcheck]  judge failed (ignored): {e}", flush=True)
        return f"Summary faithfulness: failed ({type(e).__name__}: {e}). This does not affect the pipeline status above.", False


def _click_section(db, run_id: str, doc: dict) -> str:
    """Weekly reader-click report from the aggregate click_links / click_counts docs. Never raises: a failure
    here must not suppress the heartbeat or change the pipeline's health status."""
    try:
        import click_counts
        import click_report
        import drift_history

        def summary_for(rid, run_doc):
            links = click_counts.load_link_doc(db, rid)
            if not links:
                return None
            sent = (run_doc.get("agent4_send_summary") or {}).get("sent")
            return click_report.summarize(rid, links, click_counts.load_counts(db, rid), sent)

        current = summary_for(run_id, doc)
        priors = [s for s in (summary_for(rid, d) for rid, d in
                              drift_history.recent_runs(db, run_id, config.DRIFT_BASELINE_RUNS)) if s]
        return click_report.format_section(current, priors)
    except Exception as e:
        print(f"[healthcheck]  click report failed (ignored): {e}", flush=True)
        return f"Reader clicks: failed ({type(e).__name__}: {e}). This does not affect the pipeline status above."


def _notify(message: str, healthy: bool, mode: str = "send") -> None:
    """Best-effort single email to ALERT_EMAIL, sent every run. Never touches subscriber-facing code."""
    if not ALERT_EMAIL:
        print(f"[healthcheck]  ALERT_EMAIL not set — cannot send report. Message was:\n{message}", flush=True)
        return

    status    = "all clear" if healthy else "problem detected"
    subject   = f"{NEWSLETTER_NAME} {mode} health check — {status}"
    try:
        import report_html
        html_body = report_html.render(message, healthy, mode, NEWSLETTER_NAME)
    except Exception as e:
        # Presentation must never suppress the heartbeat: fall back to the plain monospace report.
        print(f"[healthcheck]  styled report failed, sending plain text: {e}", flush=True)
        escaped   = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html_body = f"<pre style=\"font-family:monospace;white-space:pre-wrap;\">{escaped}</pre>"

    try:
        api_key = agent4_send._get_sendgrid_api_key()
        agent4_send.send_email(api_key, ALERT_EMAIL, html_body, subject)
        print(f"[healthcheck]  Report sent to {ALERT_EMAIL} ({status})", flush=True)
    except Exception as e:
        print(f"[healthcheck]  FAILED to send report email: {e}", flush=True)


def run(run_id: str, check: str | None = None) -> None:
    """Entry point. `run_id` is this health check's OWN invocation id — the
    pipeline run being checked is looked up separately below.

    Any unexpected error in the check itself is turned into a "problem
    detected" email rather than an uncaught exception — otherwise a bug in
    the health check silently suppresses the weekly heartbeat entirely, which
    is exactly how it failed before (a naive `started_at` crashed the age
    check every week for a month with no email either way)."""
    mode = _resolve_mode(check)
    try:
        _run(run_id, mode)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[healthcheck]  check itself failed: {tb}", flush=True)
        _notify(
            f"The health check itself failed with an unexpected error — the pipeline "
            f"status could not be evaluated this run:\n\n{tb}",
            healthy=False,
            mode=mode,
        )
        raise


def _run(run_id: str, mode: str = "send") -> None:
    draft_only = mode == "draft"
    print(f"[healthcheck]  Starting {mode} check (invocation run_id={run_id})", flush=True)

    if not USE_FIRESTORE:
        print("[healthcheck]  USE_FIRESTORE is false — nothing to check locally, skipping.", flush=True)
        return

    from google.cloud import firestore
    db = firestore.Client(project=GCP_PROJECT_ID)

    checked_run_id, doc = _latest_run_doc(db)
    if doc is None:
        _notify("No pipeline_runs document found at all — the pipeline may never have started this week.", healthy=False, mode=mode)
        return

    started_at_raw = doc.get("started_at")
    if started_at_raw:
        started_at = parse_started_at(started_at_raw)
        age_hours = (datetime.now(timezone.utc) - started_at).total_seconds() / 3600
        stale_after = DRAFT_STALE_AFTER_HOURS if draft_only else STALE_AFTER_HOURS
        if age_hours > stale_after:
            _notify(
                f"No recent pipeline run found — the most recent pipeline_runs document "
                f"(run_id={checked_run_id}) started {age_hours:.1f}h ago, at {started_at_raw}. "
                f"Expected a run to have started within the last {stale_after}h.",
                healthy=False,
                mode=mode,
            )
            return

    problems = _diagnose(doc, mode)
    judge_text, judge_flagged = _judge_section(db, checked_run_id, doc)
    if judge_flagged and config.JUDGE_ALERTING_ENABLED:
        problems.append("summary faithfulness dropped versus the prior weeks (see the judge section below)")
    drift_text = (f"{_drift_section(db, checked_run_id, doc)}\n\n{_usage_section(db, checked_run_id, doc)}"
                  f"\n\n{judge_text}")
    if draft_only:
        drift_text = ("DRAFT CHECK: this verifies the newsletter composed properly. It sends Monday 7:00 AM; "
                      "delivery is checked by the Monday 7:10 send report.\n\n" + drift_text)
    else:
        drift_text += f"\n\n{_click_section(db, checked_run_id, doc)}"

    if not problems:
        print(f"[healthcheck]  run_id={checked_run_id} looks healthy.", flush=True)
        _notify(f"Pipeline run {checked_run_id} (started {started_at_raw}) completed successfully. "
                f"No problems detected.\n\n{drift_text}", healthy=True, mode=mode)
        return

    body_lines = [f"Pipeline run {checked_run_id} (started {started_at_raw}) has problems:", ""]
    body_lines += [f"- {p}" for p in problems]
    body_lines += ["", drift_text]
    _notify("\n".join(body_lines), healthy=False, mode=mode)


if __name__ == "__main__":
    run(run_id="local-debug")
