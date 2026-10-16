# agents/launch.py
#
# The per-topic launch switch. A newsletter is public (listed, subscribable, archived on the site) only when its
# topic id is in the PUBLIC_TOPICS env var on agent-subscriptions; the AI newsletter is always public. A hidden
# topic still runs and sends weekly if the owner's Scheduler jobs say so, but only admins (ADMIN_EMAILS) can see
# it on the site or subscribe to it, which is how the owner reads real issues before strangers can sign up.
#
# Launching needs no redeploy:
#   gcloud run services update agent-subscriptions --update-env-vars PUBLIC_TOPICS=finance,gaming
# (Cloud Run starts new instances with the new value; the variable is read per call so tests can set it too.)

import os

import topics

ENV_VAR = "PUBLIC_TOPICS"

_warned: set = set()


def _ordered(ids) -> list[str]:
    """AI first, then by newsletter name, so every list on the site is in the same order."""
    found = [topics.get_topic(i) for i in dict.fromkeys(ids)]
    found.sort(key=lambda t: (not t.is_default, t.name.lower()))
    return [t.id for t in found]


def public_topic_ids() -> list[str]:
    """Topic ids anyone may see and subscribe to. Unknown ids in PUBLIC_TOPICS are ignored (logged once)."""
    wanted = [p.strip().lower() for p in os.environ.get(ENV_VAR, "").split(",") if p.strip()]
    ok = [topics.DEFAULT_TOPIC]
    for topic_id in wanted:
        if topics.is_valid_topic(topic_id):
            ok.append(topics.get_topic(topic_id).id)
        elif topic_id not in _warned:
            _warned.add(topic_id)
            print(f"[launch]  ignoring unknown topic {topic_id!r} in {ENV_VAR}", flush=True)
    return _ordered(ok)


def visible_topic_ids(is_admin: bool = False) -> list[str]:
    """Topic ids this caller may see: the public ones, or every known topic for an admin."""
    return _ordered(topics.list_topic_ids()) if is_admin else public_topic_ids()


def is_public(topic_id: str) -> bool:
    return topic_id in public_topic_ids()


def is_visible(topic_id: str, is_admin: bool = False) -> bool:
    return topic_id in visible_topic_ids(is_admin)
