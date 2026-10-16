"""agent1b for a non-AI topic: its own categories, prompt, languages, paywalls and article cap.
The AI path is covered by test_agent1b_graph.py and test_topics_ai_equivalence.py."""
import agent1b_fetch_news as a1b
import agent1b_graph as g
import pytest
import topics
from fakes import FakeClient   # noqa: F401  (fixtures live in tests/fakes.py)


def _topic(**over):
    raw = {
        "id": "demo", "name": "Demo Weekly", "tagline": "t", "audience": "demo readers who {like} braces",
        "accent_color": "#112233", "languages": ["en"], "variants": ["0_0"], "spotlight": "none",
        "categories": [{"name": "Alpha", "definition": "first {thing}"}, {"name": "Beta", "definition": "second"}],
        "sources": [{"type": "rss", "feeds": [{"name": "f", "url": "https://example.com/f.xml"}]}],
        "paywalled_domains": ["paywall.example"], "max_articles": 3,
        # the generic news_filter prompt (the default); the other generic prompts arrive in a later step
        "prompts": {k: v for k, v in topics.get_topic("ai").prompts.items() if k != "news_filter"},
    }
    raw.update(over)
    return topics.parse_topic(raw)


def _art(i, host="a.example", score=None):
    return {"source": "rss", "title": f"T{i}", "description": "d", "url": f"https://{host}/{i}",
            "language": "en", "hn_score": score}


# --- prefilter ------------------------------------------------------------------------------------

def test_topic_paywalls_are_added_to_the_global_list():
    t = _topic(max_articles=0)
    out = a1b.prefilter([_art(1, "paywall.example"), _art(2, "nytimes.com"), _art(3)], t)
    assert [a["title"] for a in out] == ["T3"]


def test_ai_prefilter_is_unchanged_by_the_topic_argument():
    arts = [_art(i, f"h{i % 3}.example") for i in range(30)]
    assert a1b.prefilter(arts, topics.get_topic("ai")) == a1b.prefilter(arts)


def test_cap_spreads_over_sites_and_prefers_scored():
    arts = [_art(i, "big.example") for i in range(10)] + [_art(100, "small.example"), _art(101, "other.example")]
    arts[3]["hn_score"] = 50
    out = a1b.cap_articles(arts, 4)
    hosts = [a["url"].split("/")[2] for a in out]
    assert len(out) == 4 and set(hosts) == {"big.example", "small.example", "other.example"}
    assert out[0]["title"] == "T3"                      # the scored article leads its site's queue


def test_cap_zero_or_large_keeps_everything():
    arts = [_art(i) for i in range(5)]
    assert a1b.cap_articles(arts, 0) is arts
    assert a1b.cap_articles(arts, 5) is arts


# --- language filter ------------------------------------------------------------------------------

class _LangClient:
    def __init__(self, labels):
        self.labels, self.messages = labels, self

    def create(self, **kw):
        from types import SimpleNamespace as NS
        items = [{"index": i, "language": l} for i, l in enumerate(self.labels)]
        return NS(content=[NS(input={"articles": items}, type="tool_use")], stop_reason="tool_use",
                  usage=NS(input_tokens=1, output_tokens=1))


def test_english_only_topic_drops_french():
    batch = [_art(1), _art(2), _art(3)]
    kept = a1b.language_filter_batch(batch, 1, _LangClient(["en", "fr", "other"]), allowed=("en",))
    assert [a["title"] for a in kept] == ["T1"]


def test_default_language_filter_keeps_french():
    batch = [_art(1), _art(2), _art(3)]
    kept = a1b.language_filter_batch(batch, 1, _LangClient(["en", "fr", "other"]))
    assert [a["title"] for a in kept] == ["T1", "T2"]


# --- categorize -----------------------------------------------------------------------------------

class _FilterClient:
    """Answers the categorize call and remembers what it was asked."""

    def __init__(self, answer):
        self.answer, self.calls, self.messages = answer, [], self

    def create(self, **kw):
        from types import SimpleNamespace as NS
        self.calls.append(kw)
        return NS(content=[NS(input={"articles": self.answer}, type="tool_use")], stop_reason="tool_use",
                  usage=NS(input_tokens=1, output_tokens=1))


def test_categorize_uses_the_topic_categories_and_prompt():
    t = _topic()
    client = _FilterClient([{"index": 0, "category": "Alpha"}, {"index": 1, "category": "Beta"},
                            {"index": 2, "category": "Model & Product Releases"}])   # an AI category: invalid here
    out = a1b.filter_and_categorize([_art(1), _art(2), _art(3)], client=client, topic=t)
    assert [(a["title"], a["category"]) for a in out] == [("T1", "Alpha"), ("T2", "Beta")]

    call = client.calls[0]
    enum = call["tools"][0]["input_schema"]["properties"]["articles"]["items"]["properties"]["category"]["enum"]
    assert enum == ["Alpha", "Beta"]
    prompt = call["messages"][0]["content"]
    assert "Demo Weekly" in prompt and "demo readers who {like} braces" in prompt   # braces survive format()
    assert "- Alpha: first {thing}" in prompt and "- Beta: second" in prompt
    assert "Canada & Montreal" not in prompt


def test_categorize_without_a_topic_is_the_original_ai_call():
    client = _FilterClient([{"index": 0, "category": "Safety & Alignment"}])
    out = a1b.filter_and_categorize([_art(1)], client=client)
    assert out[0]["category"] == "Safety & Alignment"
    call = client.calls[0]
    assert call["tools"][0] is a1b.FILTER_TOOL
    assert "You are an AI news curator" in call["messages"][0]["content"]


def test_ai_topic_object_gives_the_same_call_as_no_topic():
    c1, c2 = _FilterClient([]), _FilterClient([])
    a1b.filter_and_categorize([_art(1)], client=c1)
    a1b.filter_and_categorize([_art(1)], client=c2, topic=topics.get_topic("ai"))
    assert c1.calls == c2.calls


def test_confidence_is_added_to_a_topic_prompt_and_tool():
    t = _topic()
    client = _FilterClient([{"index": 0, "category": "Alpha", "confidence": 5}])
    out = a1b.filter_and_categorize([_art(1)], client=client, with_confidence=True, topic=t)
    assert out[0]["confidence"] == 5
    props = client.calls[0]["tools"][0]["input_schema"]["properties"]["articles"]["items"]["properties"]
    assert "confidence" in props and props["category"]["enum"] == ["Alpha", "Beta"]


# --- pool warning ----------------------------------------------------------------------------------

def test_pool_warning_names_the_topics_own_categories():
    t = _topic()
    note = a1b.pool_warning({"Alpha": [1] * 50, "Beta": []}, t)
    assert "Beta (0)" in note and "Alpha" not in note and "Model & Product" not in note


# --- run() ------------------------------------------------------------------------------------------

def test_single_pass_refuses_other_topics(monkeypatch):
    monkeypatch.setattr(a1b, "AGENT1B_MODE", "single_pass")
    monkeypatch.setattr(topics, "topic_for_run", lambda run_id, uf=None: _topic())
    recorded = []
    monkeypatch.setattr(a1b, "_record_failure", lambda run_id, agent, e: recorded.append(str(e)))
    with pytest.raises(RuntimeError, match="only supports the AI newsletter"):
        a1b.run("r1")
    assert recorded and "demo" in recorded[0]


def test_graph_never_reviews_another_topic(monkeypatch):
    """Even with review switched on, a non-AI topic asks for no confidence and routes nothing to review."""
    t = _topic()
    monkeypatch.setattr(a1b, "fetch_topic_articles", lambda topic: [_art(1), _art(2)])
    client = _FilterClient([{"index": 0, "category": "Alpha"}, {"index": 1, "category": "Beta"}])

    class Router(_FilterClient):
        def create(self, **kw):
            if kw["tools"][0]["name"] == "filter_by_language":
                return _LangClient(["en", "en"]).create(**kw)
            return super().create(**kw)

    client = Router([{"index": 0, "category": "Alpha"}, {"index": 1, "category": "Beta"}])
    cfg = g.ReviewConfig(max_articles=30)
    state = g.run_graph("r1", client=client, fetcher=lambda u, t: (None, "n/a"), cfg=cfg, topic=t)
    assert [a["category"] for a in state["final"]] == ["Alpha", "Beta"]
    assert state["to_review"] == [] and all(not a["routed_to_review"] for a in state["audit"])
    cat_call = [c for c in client.calls if c["tools"][0]["name"] == "filter_articles"][0]
    assert "confidence" not in cat_call["tools"][0]["input_schema"]["properties"]["articles"]["items"]["properties"]
