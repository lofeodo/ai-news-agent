# main.py
#
# Shared Cloud Run entrypoint for all agents.
# Reads AGENT_NAME env var to decide which agent to run.
#
# Valid values:
#   agent1a   → agents/agent1a_fetch_papers.py
#   agent1b   → agents/agent1b_fetch_news.py
#   agent2a   → agents/agent2a_summarize_papers.py
#   agent2b   → agents/agent2b_summarize_news.py
#   agent3    → agents/agent3_compose.py
#
# Cloud Run invokes this via HTTP POST to /.
# The agent runs in a background thread so we can return 200 immediately —
# Cloud Run has a request timeout, but our agents can take several minutes.

import os
import sys
import threading
import traceback

from fastapi import FastAPI, Response

# Make the agents/ directory importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "agents"))

app = FastAPI()

AGENT_REGISTRY = {
    "agent1a": "agent1a_fetch_papers",
    "agent1b": "agent1b_fetch_news",
    "agent2a": "agent2a_summarize_papers",
    "agent2b": "agent2b_summarize_news",
    "agent3":  "agent3_compose",
}


def _run_agent(module_name: str) -> None:
    """Import the agent module and call its run() function."""
    try:
        import importlib
        module = importlib.import_module(module_name)
        module.run()
    except Exception:
        # Exceptions in background threads won't surface to the HTTP response,
        # so we print them explicitly — they'll appear in Cloud Logging.
        traceback.print_exc()


@app.post("/")
def trigger():
    agent_name = os.environ.get("AGENT_NAME", "").strip()

    if not agent_name:
        return Response(
            content="AGENT_NAME environment variable is not set.",
            status_code=500,
        )

    module_name = AGENT_REGISTRY.get(agent_name)
    if not module_name:
        return Response(
            content=f"Unknown AGENT_NAME '{agent_name}'. Valid values: {list(AGENT_REGISTRY.keys())}",
            status_code=400,
        )

    print(f"[main]  Starting {agent_name} in background thread...")
    thread = threading.Thread(target=_run_agent, args=(module_name,), daemon=True)
    thread.start()

    return {"status": "started", "agent": agent_name}


@app.get("/health")
def health():
    """Health check endpoint — Cloud Run uses this to verify the container is up."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)