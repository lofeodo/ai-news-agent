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
