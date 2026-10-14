# agents/stage_counter.py
#
# The agent2a/agent2b completion counter. Each stage counts itself once per run, however
# many times it is run, so the second finisher is the only caller that ever gets True.
# A run whose both stages counted but whose `content-summarized` message was never published
# (the publish failed) is picked up by the next repeat of either stage: see PUBLISHED_FLAG.

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import FIRESTORE_COLLECTION, GCP_PROJECT_ID

# Set on the run doc once `content-summarized` has been published for it.
PUBLISHED_FLAG = "content_summarized_published"


def count_stage(run_id: str, flag: str) -> bool:
    """Atomically count one finished stage on the run doc; True when this call should publish `content-summarized`.

    `flag` (e.g. "agent2a_counted") is set in the same transaction as the increment. A repeat run
    of a stage that already counted changes nothing, so it can neither push the count to 2 before the
    other stage is done nor publish a second time. The one exception: when both stages have counted but
    PUBLISHED_FLAG was never set (a publish that failed), the repeat run returns True so it can publish.
    Call mark_published() after the publish succeeds.
    """
    from google.cloud import firestore

    db  = firestore.Client(project=GCP_PROJECT_ID)
    ref = db.collection(FIRESTORE_COLLECTION).document(run_id)

    @firestore.transactional
    def _count(transaction, ref):
        data = ref.get(transaction=transaction).to_dict() or {}
        count = data.get("agent2_completions", 0)
        if data.get(flag):
            return count, False, count >= 2 and not data.get(PUBLISHED_FLAG)
        transaction.update(ref, {"agent2_completions": count + 1, flag: True})
        return count + 1, True, count + 1 >= 2

    count, counted_now, publish = _count(db.transaction(), ref)
    note = "" if counted_now else " (already counted by this stage)"
    if publish and not counted_now:
        note = " (already counted, but never published: publishing now)"
    print(f"[{flag.removesuffix('_counted')}]  agent2_completions = {count}{note}")
    return publish


def mark_published(run_id: str) -> None:
    """Record that `content-summarized` went out for this run. Best effort: if this write fails, a repeat run
    may publish again, and agent3 leaves an already composed run alone."""
    try:
        from google.cloud import firestore

        firestore.Client(project=GCP_PROJECT_ID).collection(FIRESTORE_COLLECTION).document(run_id).set(
            {PUBLISHED_FLAG: True}, merge=True)
    except Exception as e:
        print(f"[stage_counter]  could not record the publish: {type(e).__name__}", flush=True)
