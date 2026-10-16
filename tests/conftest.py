import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agents"))
os.chdir(ROOT)  # agents open prompts/... relative to the repo root


import pytest


@pytest.fixture(autouse=True)
def _no_langsmith_in_tests(monkeypatch):
    """Tests must never reach the real LangSmith API, even if the developer's shell has a key set."""
    for var in ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _default_topic_in_tests(monkeypatch):
    """Local mode reads the topic from $TOPIC; a developer's shell must not change which newsletter tests build."""
    monkeypatch.delenv("TOPIC", raising=False)


@pytest.fixture
def demo_topic(monkeypatch):
    """A registered non-AI topic without a paper spotlight (id "demo"), so tests need no real profile file."""
    import topics
    t = topics.parse_topic({
        "id": "demo", "name": "Demo Weekly", "tagline": "A demo", "audience": "demo readers",
        "accent_color": "#112233", "languages": ["en"], "variants": ["0_0"], "spotlight": "none",
        "categories": [{"name": "Alpha", "definition": "first", "icon": "A"},
                       {"name": "Beta", "definition": "second", "cap": 2}],
        "sources": [{"type": "rss", "feeds": [{"name": "f", "url": "https://example.com/f.xml"}]}],
    })   # no prompts named: every one falls back to prompts/generic/
    monkeypatch.setitem(topics._CACHE, "demo", t)
    monkeypatch.setattr(topics, "list_topic_ids", lambda: ["ai", "demo"])
    return t
