# agents/tracing.py
#
# Opt-in LangSmith tracing. Enabled only when LANGSMITH_TRACING=true AND
# LANGSMITH_API_KEY are both set; otherwise everything here is a no-op and the
# plain Anthropic client is returned. Trace inputs are prompts/article text
# (public news data) — API keys only ever live in client constructors / env,
# never in traced message payloads.

import os

import anthropic


def tracing_enabled() -> bool:
    on = (os.environ.get("LANGSMITH_TRACING") or os.environ.get("LANGCHAIN_TRACING_V2") or "").lower() == "true"
    key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
    return bool(on and key)


def configure() -> bool:
    """Make tracing all-or-nothing. Returns whether tracing is active.

    If tracing was requested without a key, switch it off explicitly so
    LangGraph/langchain-core don't emit auth errors on every span.
    """
    if tracing_enabled():
        return True
    for var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        if os.environ.get(var):
            os.environ[var] = "false"
    return False


def make_client() -> anthropic.Anthropic:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_1ST_API_KEY"))
    if tracing_enabled():
        try:
            from langsmith.wrappers import wrap_anthropic
            client = wrap_anthropic(client)
        except Exception as e:  # tracing must never break the run
            print(f"  [tracing] could not wrap Anthropic client: {e}")
    return client


def flush() -> None:
    """Block until queued traces are sent (Cloud Run throttles CPU after the response)."""
    if not tracing_enabled():
        return
    try:
        from langsmith import Client
        Client().flush()
    except Exception as e:
        print(f"  [tracing] flush failed: {e}")
