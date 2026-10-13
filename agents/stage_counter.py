# agents/stage_counter.py
#
# The agent2a/agent2b completion counter. Each stage counts itself once per run, however
# many times it is run, so the second finisher is the only caller that ever gets True.

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import FIRESTORE_COLLECTION, GCP_PROJECT_ID


def count_stage(run_id: str, flag: str) -> bool:
    """Atomically count one finished stage on the run doc; True only if this call made the count 2.

    `flag` (e.g. "agent2a_counted") is set in the same transaction as the increment. A repeat run
    of a stage that already counted changes nothing and returns False, so it can neither push the
    count to 2 before the other stage is done nor publish `content-summarized` a second time.
    """
    from google.cloud import firestore

    db  = firestore.Client(project=GCP_PROJECT_ID)
    ref = db.collection(FIRESTORE_COLLECTION).document(run_id)

    @firestore.transactional
    def _count(transaction, ref):
        data = ref.get(transaction=transaction).to_dict() or {}
        count = data.get("agent2_completions", 0)
        if data.get(flag):
            return count, False
        transaction.update(ref, {"agent2_completions": count + 1, flag: True})
        return count + 1, True

    count, counted_now = _count(db.transaction(), ref)
    print(f"[{flag.removesuffix('_counted')}]  agent2_completions = {count}"
          f"{'' if counted_now else ' (already counted by this stage)'}")
    return counted_now and count >= 2
