"""agent4 copes with stored data of an unexpected shape: no prefs, repeated section names. Stubbed (no network)."""
import agent4_send as a4
from test_agent4_resume import env  # noqa: F401  (fixture)

NAMES = a4._DEFAULT_SECTIONS[:2]


def _html():
    return "<body>" + "".join(
        f"<!-- SECTION:{n} -->S-{i}<!-- /SECTION:{n} -->" for i, n in enumerate(NAMES)) + "</body>"


def test_repeated_section_names_render_once():
    out = a4._apply_section_config(_html(), {"enabled_sections": [NAMES[1], NAMES[0], NAMES[1], NAMES[0]]})
    assert out.count(f"<!-- SECTION:{NAMES[0]} -->") == 1
    assert out.count(f"<!-- SECTION:{NAMES[1]} -->") == 1
    assert out.index("S-1") < out.index("S-0")


def test_a_subscriber_without_prefs_gets_the_default_variant_and_the_send_completes(env, monkeypatch):  # noqa: F811
    db, sends, _ = env
    monkeypatch.setattr(a4, "_active_subscribers", lambda d: [
        {"email": "a@example.com", "token": "t1", "prefs": None},
        {"email": "b@example.com", "token": "t2"},
    ])
    a4.run("agent4-run")
    assert sends == ["a@example.com", "b@example.com"]
    assert db.store["pipeline_runs"]["RUN1"]["agent4_send_summary"]["failed"] == 0
