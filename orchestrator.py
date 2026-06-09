# orchestrator.py
#
# Two modes, controlled by USE_FIRESTORE env var:
#
# Local mode (USE_FIRESTORE=false, default):
#   - Generates a run_id
#   - Calls each agent's run(run_id) directly, in sequence
#   - No Pub/Sub, no Firestore
#   - Run with: python orchestrator.py
#
# Cloud mode (USE_FIRESTORE=true):
#   - Generates a run_id
#   - Creates a coordination document in Firestore
#   - Publishes a single message to the "pipeline-start" Pub/Sub topic
#   - Exits — the rest of the pipeline is event-driven from there

import json
import os
import sys
from datetime import datetime, timezone

# orchestrator.py is at the project root.
# agents/ is a subdirectory — add it to the path so we can import agent modules directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "agents"))

from config import (
    GCP_PROJECT_ID,
    TOPIC_PIPELINE_START,
    FIRESTORE_COLLECTION,
    USE_FIRESTORE,
)


def generate_run_id() -> str:
    """Generate a unique run ID based on the current UTC time."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def publish_pubsub(topic_name: str, payload: dict) -> None:
    """Publish a message to a Pub/Sub topic."""
    from google.cloud import pubsub_v1

    publisher   = pubsub_v1.PublisherClient()
    topic_path  = publisher.topic_path(GCP_PROJECT_ID, topic_name)
    data        = json.dumps(payload).encode("utf-8")
    future      = publisher.publish(topic_path, data)
    message_id  = future.result()
    print(f"[orchestrator]  Published to {topic_name} — message_id={message_id}")


def create_firestore_run_document(run_id: str) -> None:
    """Create the coordination document for this pipeline run."""
    from google.cloud import firestore

    db  = firestore.Client(project=GCP_PROJECT_ID)
    ref = db.collection(FIRESTORE_COLLECTION).document(run_id)
    ref.set({
        "run_id":             run_id,
        "started_at":         datetime.now(timezone.utc).isoformat(),
        "status":             "running",
        "agent2_completions": 0,
    })
    print(f"[orchestrator]  Firestore document created for run_id={run_id}")


def run_local(run_id: str) -> None:
    """Run all agents sequentially in-process. Local dev only."""
    import agent1a_fetch_papers
    import agent1b_fetch_news
    import agent2a_summarize_papers
    import agent2b_summarize_news
    import agent3_compose

    print(f"\n[orchestrator]  Local sequential run — run_id={run_id}\n")

    print("[orchestrator]  Step 1/5 — agent1a (fetch + score papers)")
    agent1a_fetch_papers.run(run_id)

    print("\n[orchestrator]  Step 2/5 — agent1b (fetch + filter news)")
    agent1b_fetch_news.run(run_id)

    print("\n[orchestrator]  Step 3/5 — agent2a (summarize papers)")
    agent2a_summarize_papers.run(run_id)

    print("\n[orchestrator]  Step 4/5 — agent2b (summarize news)")
    agent2b_summarize_news.run(run_id)

    print("\n[orchestrator]  Step 5/5 — agent3 (compose + send)")
    agent3_compose.run(run_id)

    print(f"\n[orchestrator]  Pipeline complete — run_id={run_id}")


def run_cloud(run_id: str) -> None:
    """Create Firestore coordination document and publish pipeline-start event."""
    print(f"\n[orchestrator]  Cloud run — run_id={run_id}\n")

    create_firestore_run_document(run_id)
    publish_pubsub(TOPIC_PIPELINE_START, {"run_id": run_id})

    print("[orchestrator]  Pipeline started — agents will trigger each other via Pub/Sub")


def run(run_id: str) -> None:
    """Entry point called by main.py when deployed as a Cloud Run service."""
    run_cloud(run_id)


if __name__ == "__main__":
    run_id = generate_run_id()

    if USE_FIRESTORE:
        run_cloud(run_id)
    else:
        run_local(run_id)