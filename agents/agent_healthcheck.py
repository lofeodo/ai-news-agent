# agents/agent_healthcheck.py
#
# Standalone check, independent of the fetch->summarize->compose->send chain.
# Triggered by its own Cloud Scheduler job (7:10 AM Monday, shortly after
# agent4's 7:00 AM send) rather than by Pub/Sub, so it has no run_id for the
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
from config import GCP_PROJECT_ID, FIRESTORE_COLLECTION, USE_FIRESTORE, ALERT_EMAIL

import agent4_send  # reuse send_email() / _get_sendgrid_api_key() only

NEWSLETTER_NAME = "Latent SpaceMail"

# How stale the latest pipeline_runs doc can be before we treat it as "no
# run happened this week" rather than evaluating its (old) completion state.
STALE_AFTER_HOURS = 4


def _parse_started_at(raw: str) -> datetime:
    """Parse a pipeline_runs `started_at` string to an aware UTC datetime.

    The field is written by whichever agent creates the run doc, and not all
    of them write an offset: orchestrator.py uses datetime.now(timezone.utc)
    (offset-aware), but agent1a writes datetime.now().isoformat() (naive). A
    naive value here is assumed to be UTC — Cloud Run containers run in UTC.
    """
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

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


def _diagnose(doc: dict) -> list[str]:
    """Return human-readable problem descriptions for one pipeline_runs doc. Empty means healthy."""
    problems = []

    for agent in ERROR_AGENTS:
        error = doc.get(f"{agent}_error")
        if error:
            failed_at = doc.get(f"{agent}_failed_at", "unknown time")
            problems.append(f"{agent} failed at {failed_at}: {error}")

    for field, label in EXPECTED_STAGES:
        if not doc.get(field):
            problems.append(f"{label} never completed — '{field}' missing from pipeline_runs")

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


def _notify(message: str, healthy: bool) -> None:
    """Best-effort single email to ALERT_EMAIL, sent every run. Never touches subscriber-facing code."""
    if not ALERT_EMAIL:
        print(f"[healthcheck]  ALERT_EMAIL not set — cannot send report. Message was:\n{message}", flush=True)
        return

    status    = "all clear" if healthy else "problem detected"
    subject   = f"{NEWSLETTER_NAME} pipeline health check — {status}"
    escaped   = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html_body = f"<pre style=\"font-family:monospace;white-space:pre-wrap;\">{escaped}</pre>"

    try:
        api_key = agent4_send._get_sendgrid_api_key()
        agent4_send.send_email(api_key, ALERT_EMAIL, html_body, subject)
        print(f"[healthcheck]  Report sent to {ALERT_EMAIL} ({status})", flush=True)
    except Exception as e:
        print(f"[healthcheck]  FAILED to send report email: {e}", flush=True)


def run(run_id: str) -> None:
    """Entry point. `run_id` is this health check's OWN invocation id — the
    pipeline run being checked is looked up separately below.

    Any unexpected error in the check itself is turned into a "problem
    detected" email rather than an uncaught exception — otherwise a bug in
    the health check silently suppresses the weekly heartbeat entirely, which
    is exactly how it failed before (a naive `started_at` crashed the age
    check every week for a month with no email either way)."""
    try:
        _run(run_id)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[healthcheck]  check itself failed: {tb}", flush=True)
        _notify(
            f"The health check itself failed with an unexpected error — the pipeline "
            f"status could not be evaluated this run:\n\n{tb}",
            healthy=False,
        )
        raise


def _run(run_id: str) -> None:
    print(f"[healthcheck]  Starting check (invocation run_id={run_id})", flush=True)

    if not USE_FIRESTORE:
        print("[healthcheck]  USE_FIRESTORE is false — nothing to check locally, skipping.", flush=True)
        return

    from google.cloud import firestore
    db = firestore.Client(project=GCP_PROJECT_ID)

    checked_run_id, doc = _latest_run_doc(db)
    if doc is None:
        _notify("No pipeline_runs document found at all — the pipeline may never have started this week.", healthy=False)
        return

    started_at_raw = doc.get("started_at")
    if started_at_raw:
        started_at = _parse_started_at(started_at_raw)
        age_hours = (datetime.now(timezone.utc) - started_at).total_seconds() / 3600
        if age_hours > STALE_AFTER_HOURS:
            _notify(
                f"No recent pipeline run found — the most recent pipeline_runs document "
                f"(run_id={checked_run_id}) started {age_hours:.1f}h ago, at {started_at_raw}. "
                f"Expected a run to have started within the last {STALE_AFTER_HOURS}h.",
                healthy=False,
            )
            return

    problems = _diagnose(doc)

    if not problems:
        print(f"[healthcheck]  run_id={checked_run_id} looks healthy.", flush=True)
        _notify(f"Pipeline run {checked_run_id} (started {started_at_raw}) completed successfully. No problems detected.", healthy=True)
        return

    body_lines = [f"Pipeline run {checked_run_id} (started {started_at_raw}) has problems:", ""]
    body_lines += [f"- {p}" for p in problems]
    _notify("\n".join(body_lines), healthy=False)


if __name__ == "__main__":
    run(run_id="local-debug")
