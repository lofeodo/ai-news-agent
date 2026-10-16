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

import argparse
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
from topics import DEFAULT_TOPIC, check_run_request


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


def create_firestore_run_document(run_id: str, kind: str = "debug", topic: str | None = None) -> None:
    """Create the coordination document for this pipeline run.

    `topic` is written only for a non-AI topic: a doc with no `topic` is an AI run, so the AI newsletter's
    docs stay exactly as they were before topics existed.
    """
    from google.cloud import firestore

    db  = firestore.Client(project=GCP_PROJECT_ID)
    ref = db.collection(FIRESTORE_COLLECTION).document(run_id)
    doc = {
        "run_id":             run_id,
        "started_at":         datetime.now(timezone.utc).isoformat(),
        "status":             "running",
        "run_kind":           kind,
        "agent2_completions": 0,
    }
    if topic and topic != DEFAULT_TOPIC:
        doc["topic"] = topic
    ref.set(doc)
    print(f"[orchestrator]  Firestore document created for run_id={run_id} (run_kind={kind}, topic={topic or DEFAULT_TOPIC})")


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


def run_cloud(run_id: str, kind: str = "debug", topic: str | None = None) -> None:
    """Create Firestore coordination document and publish pipeline-start event."""
    print(f"\n[orchestrator]  Cloud run — run_id={run_id}\n")

    create_firestore_run_document(run_id, kind, topic)
    publish_pubsub(TOPIC_PIPELINE_START, {"run_id": run_id})

    print("[orchestrator]  Pipeline started — agents will trigger each other via Pub/Sub")


def run(run_id: str, kind: str = "debug", topic: str | None = None) -> None:
    """Entry point called by main.py when deployed as a Cloud Run service.

    kind is "release" (the official weekly run: published and mailed) or "debug" (the default: previewable only).
    topic names the newsletter (a topics/<id>.yaml profile); none means the AI newsletter. main.py already refuses
    a bad topic or a non-AI release; the check is repeated here so no caller can create such a run.
    """
    problem = check_run_request(topic, kind)
    if problem:
        raise ValueError(problem)
    run_cloud(run_id, kind, topic)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start a pipeline run (local sequential, or cloud when USE_FIRESTORE=true).")
    parser.add_argument("--topic", default=os.environ.get("TOPIC") or DEFAULT_TOPIC,
                        help="newsletter topic id, a topics/<id>.yaml profile (default: ai)")
    args = parser.parse_args()
    problem = check_run_request(args.topic, "debug")
    if problem:
        parser.error(problem)
    # Local agents read the topic from the environment (cloud agents read it from the run doc).
    os.environ["TOPIC"] = args.topic

    run_id = generate_run_id()

    if USE_FIRESTORE:
        run_cloud(run_id, topic=args.topic)
    else:
        run_local(run_id)