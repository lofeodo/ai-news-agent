"""agent3 dedup graph: remove duplicates, refill from runners-up, degrade on failure (no network)."""
from agent3_dedup_graph import DedupConfig, dedup_section, keep_index
from fakes import ScriptedClient, response, tool_use

CAT = "Industry & Business"      # cap 3


def art(title, hn=None):
    return {"title": title, "url": f"https://x/{title}", "summary": f"about {title}", "hn_score": hn}


def report(*groups):
    return response(tool_use("report_duplicates", groups=[{"indices": list(g), "reason": "same"} for g in groups]))


def titles(arts):
    return [a["title"] for a in arts]


def run(client, picks, pool, **kw):
    return dedup_section(CAT, picks, pool, client, cfg=kw.pop("cfg", DedupConfig(mode="graph", max_iterations=6)), **kw)


def test_no_duplicates_one_call_picks_unchanged():
    c = ScriptedClient(report())
    picks = [art("A"), art("B"), art("C")]
    r = run(c, picks, [art("R0")])
    assert titles(r.picks) == ["A", "B", "C"] and len(c.calls) == 1
    assert r.audit["status"] == "ok" and r.audit["after"] == 3


def test_duplicate_removed_lower_hn_and_refilled():
    c = ScriptedClient(report([0, 1]), report())
    picks = [art("A", 10), art("B", 200), art("C")]
    r = run(c, picks, [art("R0")])
    assert sorted(titles(r.picks)) == ["B", "C", "R0"]
    assert r.audit["groups"][0]["kept"]["title"] == "B"
    assert r.audit["groups"][0]["removed"][0]["title"] == "A"
    assert r.audit["fallbacks"][0]["accepted"] is True and len(c.calls) == 2


def test_tie_keeps_earliest():
    assert keep_index((0, 2), [art("A", 5), art("B"), art("C", 5)]) == 0


def test_fallback_duplicating_kept_article_is_dropped_next_tried():
    c = ScriptedClient(report([0, 1]), report([0, 3]), report())   # R0 (#3) dups kept #0? A,B dup -> A kept (#0)
    picks = [art("A", 9), art("B"), art("C")]
    r = run(c, picks, [art("R0"), art("R1")])
    assert sorted(titles(r.picks)) == ["A", "C", "R1"]
    fb = r.audit["fallbacks"]
    assert [f["accepted"] for f in fb] == [False, True]
    assert fb[0]["duplicate_of"]["title"] == "A"


def test_fallback_matching_removed_article_redirects_to_kept_member():
    # B (#1) removed in favour of A (#0); R0 (#3) is flagged against B (#1) -> counted as dup of A
    c = ScriptedClient(report([0, 1]), report([1, 3]), report())
    picks = [art("A", 9), art("B"), art("C")]
    r = run(c, picks, [art("R0"), art("R1")])
    assert "R0" not in titles(r.picks) and "R1" in titles(r.picks)
    assert r.audit["fallbacks"][0]["duplicate_of"]["title"] == "A"


def test_pool_exhausted_section_stays_short():
    c = ScriptedClient(report([0, 1]))
    r = run(c, [art("A", 1), art("B"), art("C")], [])
    assert sorted(titles(r.picks)) == ["A", "C"] and r.audit["after"] == 2


def test_iteration_limit_stops_loop():
    c = ScriptedClient(report([0, 1]), report([0, 3]), report([0, 4]), report([0, 5]))
    pool = [art(f"R{i}") for i in range(5)]
    r = run(c, [art("A", 9), art("B"), art("C")], pool, cfg=DedupConfig(mode="graph", max_iterations=2))
    assert len(r.audit["fallbacks"]) == 2 and len(c.calls) == 3 and len(r.picks) == 2


def test_error_on_start_returns_original_picks_degraded():
    c = ScriptedClient(RuntimeError("down"))
    picks = [art("A"), art("B"), art("C")]
    r = run(c, picks, [art("R0")])
    assert r.picks == picks and r.audit["status"] == "degraded" and "down" in r.audit["error"]


def test_error_on_add_returns_original_picks_degraded():
    c = ScriptedClient(report([0, 1]), RuntimeError("down"))
    picks = [art("A", 9), art("B"), art("C")]
    r = run(c, picks, [art("R0")])
    assert titles(r.picks) == ["A", "B", "C"] and r.audit["status"] == "degraded"


def test_off_mode_makes_no_calls():
    c = ScriptedClient()
    picks = [art("A"), art("B")]
    r = run(c, picks, [], cfg=DedupConfig(mode="off"))
    assert r.picks == picks and c.calls == [] and r.audit["status"] == "off"


def test_fewer_than_two_picks_skipped():
    c = ScriptedClient()
    r = run(c, [art("A")], [art("R0")])
    assert titles(r.picks) == ["A"] and c.calls == [] and r.audit["status"] == "skipped"


def test_usage_totals_and_injected_create():
    seen = []
    c = ScriptedClient(report([0, 1]), report())

    def create(**kw):
        seen.append(kw["model"])
        return c.create(**kw)

    r = run(c, [art("A", 9), art("B"), art("C")], [art("R0")], create=create, run_id="r1", pass_name="all")
    assert r.usage == {"input_tokens": 20, "output_tokens": 10} and len(seen) == 2
    assert r.audit["pass"] == "all"
