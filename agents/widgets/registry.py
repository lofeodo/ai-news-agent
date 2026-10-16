# agents/widgets/registry.py
#
# Every widget registers itself here (@register on its class). The registry is what the newsletter definition
# validator (newsletter_def.parse(widgets=...)), the renderer plug-in and the topic profiles look widgets up in.

from .base import KINDS, SCOPES, Widget

_REGISTRY: dict[str, Widget] = {}


def register(cls):
    """Class decorator: instantiate and register a widget. Ids are unique."""
    w = cls()
    if not w.id or w.id in _REGISTRY:
        raise ValueError(f"widget id {w.id!r} is empty or already registered")
    if w.scope not in SCOPES or w.kind not in KINDS:
        raise ValueError(f"widget {w.id!r}: bad scope {w.scope!r} or kind {w.kind!r}")
    if w.scope == "personal":
        w.premium = True
    _REGISTRY[w.id] = w
    return cls


def unregister(widget_id: str) -> None:
    """For tests that register a throwaway widget."""
    _REGISTRY.pop(widget_id, None)


def get(widget_id: str) -> Widget | None:
    return _REGISTRY.get(widget_id)


def all_widgets() -> list[Widget]:
    return list(_REGISTRY.values())


def available_for(widget: Widget, topic_id: str | None) -> bool:
    return topic_id is None or "*" in widget.topics or topic_id in widget.topics


def for_topic(topic_id: str) -> list[Widget]:
    """Edition widgets a topic's profile may name."""
    return [w for w in _REGISTRY.values() if w.scope == "edition" and available_for(w, topic_id)]


def catalog(topic: str | None = None, for_custom: bool = True) -> list[dict]:
    """Widget metadata for the studio (C6). For custom newsletters (the paid product) a widget whose source
    forbids commercial use is never listed, and neither is one whose key is not set in this process."""
    out = []
    for w in _REGISTRY.values():
        if for_custom and not w.commercial_use:
            continue
        if w.missing_keys() or not available_for(w, topic):
            continue
        out.append(w.info())
    return out


def definitions_catalog() -> dict:
    """The mapping newsletter_def.parse(widgets=...) expects (it reads scope, topics, premium, commercial_use)."""
    return dict(_REGISTRY)
