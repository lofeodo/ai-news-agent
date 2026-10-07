"""Styled health check email: structure, escaping, graceful fallback (no network)."""
import agent_healthcheck as hc
import report_html as rh

USAGE = """LLM usage this run: 1,724,309 tokens (1,626,810 in / 97,499 out, 690 calls)
  cost: LangSmith $2.11 | list-price estimate $2.11 (Haiku list price as of 2026-10-05)
  - agent1a: 421,149 tokens (~$0.47)
  - agent2b: 684,745 tokens (~$0.86)
Usage drift: not enough history yet (0 archived prior runs, need 3)."""

HEALTHY = ("Pipeline run R1 (started 2026-10-07T18:42:52+00:00) completed successfully. No problems detected.\n\n"
           "DRAFT CHECK: verifies the composed newsletter.\n\n"
           "Drift check: not enough history yet (2 usable prior runs, need 3).\n\n" + USAGE +
           "\n\nSummary faithfulness: skipped (no key).")

PROBLEMS = ("Pipeline run R2 (started 2026-10-12T16:00:03+00:00) has problems:\n\n"
            "- agent2b failed at t: crashed\n- agent4 ran but sent 0/21 emails (failed=21)\n\n"
            "Drift check vs prior 4 runs: DRIFT FLAGGED\n  - confidence: DRIFT (mean 3.10 vs 4.05)\n")


def test_healthy_report_has_status_sections_and_usage_bars():
    out = rh.render(HEALTHY, True, "draft")
    assert "All clear" in out and "draft health check" in out
    assert "R1" in out and "DRAFT CHECK" in out
    assert "1.72M" in out and "690" in out and "$2.11" in out      # stat tiles
    assert "agent1a" in out and "421,149" in out                   # per-agent bar rows
    assert "Skipped" in out and "Warming up".lower() in out.lower()


def test_problem_report_lists_each_problem_and_flags_drift():
    out = rh.render(PROBLEMS, False, "send")
    assert "Problem detected" in out and "2 flagged" in out
    assert "agent2b failed at t: crashed" in out and "sent 0/21" in out
    assert "drift flagged" in out.lower()


def test_unrecognised_message_is_shown_verbatim_not_dropped():
    out = rh.render("The health check itself failed: <b>boom</b>\n\nTraceback x", False, "draft")
    assert "Problem detected" in out and "Traceback x" in out


def test_report_text_is_html_escaped():
    msg = "Pipeline run <script>alert(1)</script> (started <img src=x>) completed successfully. No problems detected."
    out = rh.render(msg, True, "send")
    assert "<script>" not in out and "<img src=x>" not in out
    out2 = rh.render("Drift check: <script>x</script>", True, "send")
    assert "<script>x" not in out2


def test_notify_sends_the_styled_html_and_falls_back_on_render_error(monkeypatch):
    sent = []
    monkeypatch.setattr(hc, "ALERT_EMAIL", "a@example.com")
    monkeypatch.setattr(hc.agent4_send, "_get_sendgrid_api_key", lambda: "k")
    monkeypatch.setattr(hc.agent4_send, "send_email", lambda key, to, body, subject: sent.append((body, subject)))
    hc._notify(HEALTHY, True, "draft")
    assert "<table" in sent[0][0] and "all clear" in sent[0][1]

    monkeypatch.setattr(rh, "render", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad")))
    hc._notify(HEALTHY, True, "draft")
    assert sent[1][0].startswith("<pre")  # plain fallback still delivers the heartbeat
