import json

import pytest

import agent1b_fetch_news as a1b
import agent1b_graph as g
import tracing
from fakes import CATS, FakeClient, Fetcher, make_articles

ORIGINAL_TOP_KEYS = {"run_at", "elapsed_seconds", "total_fetched", "total_selected", "by_category", "articles"}
ORIGINAL_ARTICLE_KEYS = {"source", "title", "description", "url", "language", "hn_score", "category"}

REAL_RUN_GRAPH = g.run_graph
CONF = {"T0": 5, "T1": 5, "T2": 2, "T3": 3}


@pytest.fixture(autouse=True)
def stub_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(a1b, "fetch_hn_articles", lambda: make_articles(4))
    monkeypatch.setattr(a1b, "fetch_newsapi_articles", lambda: [])
    monkeypatch.setattr(a1b, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(a1b, "USE_FIRESTORE", False)


def run(client, fetcher=None, **cfg):
    base = {"confidence_threshold": 4, "max_articles": 30, "max_iterations": 3, "fetch_timeout": 1}
    return g.run_graph("t", client=client, fetcher=fetcher or Fetcher(), cfg=g.ReviewConfig(**{**base, **cfg}))


def rows(state):
    return {r["url"].rsplit("/", 1)[1]: r for r in state["audit"]}


def test_routing_low_confidence_reviewed_high_finalized():
    client = FakeClient(CONF)
    state = run(client)
    r = rows(state)
    assert [r[i]["routed_to_review"] for i in "0123"] == [False, False, True, True]
    assert r["0"]["review_status"] == "skipped" and r["0"]["final_category"] == CATS[1]
    assert r["2"]["review_status"] == "reviewed" and r["2"]["final_category"] == CATS[5]
    assert r["2"]["first_pass_category"] == CATS[1]
    assert client.n("review") == 2
    assert len(state["final"]) == 4


def test_missing_confidence_is_reviewed():
    state = run(FakeClient({**CONF, "T0": None}))
    assert rows(state)["0"]["routed_to_review"] is True


def test_review_cap_picks_least_confident_and_marks_rest_capped():
    client = FakeClient(CONF)
    state = run(client, max_articles=1)
    r = rows(state)
    assert r["2"]["review_status"] == "reviewed"
    assert r["3"]["review_status"] == "capped" and r["3"]["final_category"] == CATS[1]
    assert client.n("review") == 1


def test_cap_zero_disables_review():
    client = FakeClient(CONF)
    state = run(client, max_articles=0)
    assert client.n("review") == 0
    assert all(not row["routed_to_review"] for row in state["audit"])


def test_tool_loop_fetches_then_submits():
    client, fetcher = FakeClient(CONF, review="fetch_then_submit"), Fetcher()
    state = run(client, fetcher)
    assert client.n("review") == 4          # 2 articles x (fetch turn + submit turn)
    assert len(fetcher.urls) == 2
    assert rows(state)["2"]["tool_calls"] == 1
    assert state["tool_call_counts"] == {"fetch_article_text": 2}


def test_tool_loop_stops_at_iteration_cap_and_degrades():
    client = FakeClient(CONF, review="always_fetch")
    state = run(client, max_iterations=3)
    assert client.n("review") == 3 * 2       # never more than 3 LLM calls per article
    row = rows(state)["2"]
    assert row["review_status"] == "review_failed" and row["final_category"] == CATS[1]


def test_last_iteration_forces_submit_tool():
    client = FakeClient(CONF, review="always_fetch")
    run(client, max_iterations=2)
    choices = [kw["tool_choice"] for k, kw in client.calls if k == "review"]
    assert {"type": "tool", "name": "submit_category"} in choices
    assert {"type": "any"} in choices


@pytest.mark.parametrize("fetcher", [
    Fetcher(result=(None, "fetch_error")),
    Fetcher(raises=RuntimeError("boom")),
])
def test_fetch_failure_degrades_gracefully(fetcher):
    state = run(FakeClient(CONF, review="fetch_then_submit"), fetcher)
    row = rows(state)["2"]
    assert row["review_status"] == "review_failed"
    assert row["final_category"] == row["first_pass_category"]
    assert len(state["final"]) == 4          # run still completes


def test_llm_failure_in_review_degrades_gracefully():
    state = run(FakeClient(CONF, review="raise"))
    row = rows(state)["2"]
    assert row["review_status"] == "review_failed" and "llm_error" in row["review_reason"]


def test_token_usage_tracked_per_node():
    state = run(FakeClient(CONF))
    usage = state["token_usage"]
    assert usage["language_filter"]["calls"] >= 1
    assert usage["categorize"]["input"] > 0
    assert usage["review"]["calls"] == 2
    assert rows(state)["2"]["input_tokens"] == 10


def test_final_articles_have_original_shape_no_confidence():
    state = run(FakeClient(CONF))
    for art in state["final"]:
        assert set(art) == ORIGINAL_ARTICLE_KEYS


def _run_agent(monkeypatch, tmp_path, mode, client):
    monkeypatch.setattr(a1b, "AGENT1B_MODE", mode)
    monkeypatch.setattr(a1b.anthropic, "Anthropic", lambda **kw: client)   # single-pass builds its own client
    monkeypatch.setattr(g, "run_graph", lambda run_id: REAL_RUN_GRAPH(run_id, client=client, fetcher=Fetcher(),
                                                             cfg=g.ReviewConfig(fetch_timeout=1)))
    a1b.run("t")
    with open(tmp_path / "news_filtered.json", encoding="utf-8") as f:
        return json.load(f)


def test_output_file_schema_matches_single_pass(monkeypatch, tmp_path):
    single = _run_agent(monkeypatch, tmp_path, "single_pass", FakeClient(CONF))
    assert not (tmp_path / "agent1b_review_log.json").exists()
    graph = _run_agent(monkeypatch, tmp_path, "graph", FakeClient(CONF))

    for out in (single, graph):
        assert set(out) == ORIGINAL_TOP_KEYS
        assert all(set(a) == ORIGINAL_ARTICLE_KEYS for a in out["articles"])
        assert all(set(a) == ORIGINAL_ARTICLE_KEYS for arts in out["by_category"].values() for a in arts)
    assert single["total_selected"] == graph["total_selected"] == 4
    assert {a["url"] for a in single["articles"]} == {a["url"] for a in graph["articles"]}

    with open(tmp_path / "agent1b_review_log.json", encoding="utf-8") as f:
        log = json.load(f)
    assert set(log) == {"run_id", "summary", "articles"}
    assert log["summary"]["routed_to_review"] == 2


def test_single_pass_is_untouched_by_confidence(monkeypatch):
    client = FakeClient(CONF)
    monkeypatch.setattr(a1b.anthropic, "Anthropic", lambda **kw: client)
    _, filtered = a1b.collect_and_categorize_single_pass()
    assert len(filtered) == 4 and all("confidence" not in a for a in filtered)
    assert client.n("review") == 0


def test_unexpected_error_is_recorded_and_reraised(monkeypatch):
    recorded = []
    monkeypatch.setattr(a1b, "AGENT1B_MODE", "graph")
    monkeypatch.setattr(a1b, "_record_failure", lambda run_id, agent, e: recorded.append((run_id, agent, str(e))))

    def boom():
        raise RuntimeError("hn down")

    monkeypatch.setattr(a1b, "fetch_hn_articles", boom)
    monkeypatch.setattr(tracing, "make_client", lambda: FakeClient(CONF))
    with pytest.raises(RuntimeError, match="hn down"):
        a1b.run("run-x")
    assert recorded == [("run-x", "agent1b", "hn down")]


def test_merge_counts():
    assert g.merge_counts({"a": {"x": 1}}, {"a": {"x": 2, "y": 1}, "b": 3}) == {"a": {"x": 3, "y": 1}, "b": 3}


def test_review_config_from_env(monkeypatch):
    monkeypatch.setenv("REVIEW_MAX_ARTICLES", "7")
    monkeypatch.setenv("REVIEW_CONFIDENCE_THRESHOLD", "junk")
    cfg = g.ReviewConfig.from_env()
    assert cfg.max_articles == 7 and cfg.confidence_threshold == 4


def test_mermaid_shows_review_loop():
    m = g.mermaid()
    assert "llm_call" in m and "tool_exec" in m and "finalize" in m
