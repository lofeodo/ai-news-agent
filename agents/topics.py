# agents/topics.py
#
# Topic profiles: everything that makes one newsletter different from another (sources, categories, prompts,
# title, look) lives in topics/<id>.yaml. The pipeline reads the profile named on the run doc; no `topic`
# on a run means "ai", whose profile reproduces the original AI newsletter exactly.

import os
import re
from dataclasses import dataclass, field

import yaml

ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOPICS_DIR = os.path.join(ROOT, "topics")

DEFAULT_TOPIC = "ai"

KNOWN_VARIANTS = {
    "0_0": {"include_french": False, "include_canada": False},
    "1_0": {"include_french": True,  "include_canada": False},
    "0_1": {"include_french": False, "include_canada": True},
    "1_1": {"include_french": True,  "include_canada": True},
}
KNOWN_LANGUAGES = {"en", "fr"}
KNOWN_SPOTLIGHTS = {"papers", "none"}
KNOWN_SOURCE_TYPES = {"hn", "newsapi", "rss"}
# A category with this flag only appears in variants whose preference of the same name is on.
VARIANT_FLAGS = {"include_canada"}

MAX_CATEGORIES = 7
DEFAULT_SECTION_CAP = 3
DEFAULT_MAX_ARTICLES = 600   # cap after prefilter for topics that set none (AI sets 0 = no cap)

# Prompt keys and the generic template each falls back to when a profile names none.
PROMPT_KEYS = {
    "news_filter":           "prompts/generic/news_filter_prompt.txt",
    "selection":             "prompts/generic/article_selection_prompt.txt",
    "intro":                 "prompts/generic/intro_prompt.txt",
    "news_summary":          "prompts/generic/news_summary_prompt.txt",
    "news_summary_fallback": "prompts/generic/news_summary_fallback_prompt.txt",
}

_ID_RE    = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class TopicError(ValueError):
    """A topic profile is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class Category:
    name: str
    definition: str
    cap: int = DEFAULT_SECTION_CAP
    icon: str = "📌"
    variant_flag: str | None = None


@dataclass(frozen=True)
class Topic:
    id: str
    name: str
    tagline: str
    audience: str
    accent_color: str
    languages: tuple
    variants: tuple
    spotlight: str
    max_articles: int
    categories: tuple
    sources: tuple
    paywalled_domains: tuple = ()
    prompts: dict = field(default_factory=dict)
    guidelines: str = ""    # extra editorial guidance for the categorize and selection prompts
    disclaimer: str = ""    # one line in the email footer (e.g. "not financial advice")

    @property
    def category_names(self) -> list[str]:
        return [c.name for c in self.categories]

    @property
    def is_default(self) -> bool:
        return self.id == DEFAULT_TOPIC

    def category(self, name: str) -> Category | None:
        return next((c for c in self.categories if c.name == name), None)

    def section_cap(self, name: str) -> int:
        c = self.category(name)
        return c.cap if c else DEFAULT_SECTION_CAP

    def category_definitions(self) -> str:
        """The "- Name: definition" lines used inside the generic prompts."""
        return "\n".join(f"- {c.name}: {c.definition}" for c in self.categories)

    def prompt_path(self, key: str) -> str:
        """Repo-relative path of a prompt template for this topic."""
        return self.prompts.get(key) or PROMPT_KEYS[key]

    def read_prompt(self, key: str) -> str:
        with open(os.path.join(ROOT, self.prompt_path(key)), "r", encoding="utf-8") as f:
            return f.read()

    def prompt_vars(self) -> dict:
        """Extra str.format fields every prompt template may use. Templates that don't use them ignore them."""
        return {
            "topic_name":           self.name,
            "audience":             self.audience,
            "category_definitions": self.category_definitions(),
            "guidelines":           self.guidelines or "No extra guidance.",
        }

    def fill_prompt(self, template: str) -> str:
        """Put this topic's name, audience and category definitions into a generic prompt template.

        Braces in the inserted text are doubled, because the caller runs str.format() on the template afterwards.
        A template with none of those placeholders (every AI prompt) comes back unchanged.
        """
        for key, value in self.prompt_vars().items():
            template = template.replace("{" + key + "}", value.replace("{", "{{").replace("}", "}}"))
        return template

    def read_filled_prompt(self, key: str) -> str:
        return self.fill_prompt(self.read_prompt(key))

    def variant_prefs(self) -> dict:
        return {key: KNOWN_VARIANTS[key] for key in self.variants}

    def category_active(self, category: Category | str, prefs: dict) -> bool:
        c = self.category(category) if isinstance(category, str) else category
        return not (c and c.variant_flag and not prefs.get(c.variant_flag))


def _require(cond: bool, topic_id: str, message: str) -> None:
    if not cond:
        raise TopicError(f"topic {topic_id!r}: {message}")


def _str(raw: dict, key: str, topic_id: str) -> str:
    value = raw.get(key)
    _require(isinstance(value, str) and value.strip() != "", topic_id, f"{key!r} must be a non-empty string")
    return value.strip()


def _parse_category(raw, topic_id: str) -> Category:
    _require(isinstance(raw, dict), topic_id, "each category must be a mapping")
    name = _str(raw, "name", topic_id)
    cap = raw.get("cap", DEFAULT_SECTION_CAP)
    _require(isinstance(cap, int) and not isinstance(cap, bool) and cap >= 1, topic_id,
             f"category {name!r}: cap must be a positive integer")
    flag = raw.get("variant_flag")
    _require(flag is None or flag in VARIANT_FLAGS, topic_id, f"category {name!r}: unknown variant_flag {flag!r}")
    return Category(name=name, definition=_str(raw, "definition", topic_id), cap=cap,
                    icon=str(raw.get("icon") or "📌"), variant_flag=flag)


def _parse_source(raw, topic_id: str) -> dict:
    _require(isinstance(raw, dict), topic_id, "each source must be a mapping")
    kind = raw.get("type")
    _require(kind in KNOWN_SOURCE_TYPES, topic_id, f"unknown source type {kind!r}")
    if kind == "newsapi":
        queries = raw.get("queries")
        _require(isinstance(queries, list) and queries and all(isinstance(q, str) and q.strip() for q in queries),
                 topic_id, "newsapi source needs a non-empty list of query strings")
    elif kind == "rss":
        feeds = raw.get("feeds")
        _require(isinstance(feeds, list) and feeds, topic_id, "rss source needs a non-empty feeds list")
        for feed in feeds:
            _require(isinstance(feed, dict) and isinstance(feed.get("name"), str) and feed["name"].strip(),
                     topic_id, "each feed needs a name")
            url = feed.get("url")
            _require(isinstance(url, str) and url.startswith(("https://", "http://")), topic_id,
                     f"feed {feed.get('name')!r} needs an http(s) url")
            mi = feed.get("max_items")
            _require(mi is None or (isinstance(mi, int) and not isinstance(mi, bool) and mi >= 1), topic_id,
                     f"feed {feed['name']!r}: max_items must be a positive integer")
    elif kind == "hn":
        # No queries = the week's top stories overall (the AI newsletter). With queries, each is searched
        # separately (Algolia has no OR operator) and the results are merged.
        queries = raw.get("queries")
        _require(queries is None or (isinstance(queries, list) and queries
                                     and all(isinstance(q, str) and q.strip() for q in queries)),
                 topic_id, "hn queries must be a non-empty list of strings")
        mp = raw.get("min_points")
        _require(mp is None or (isinstance(mp, int) and not isinstance(mp, bool) and mp >= 0), topic_id,
                 "hn min_points must be a non-negative integer")
    return raw


def parse_topic(raw: dict, expected_id: str | None = None) -> Topic:
    """Validate a raw profile mapping and build a Topic. Raises TopicError on any problem."""
    _require(isinstance(raw, dict), str(expected_id), "profile must be a mapping")
    topic_id = raw.get("id")
    _require(isinstance(topic_id, str) and _ID_RE.match(topic_id) is not None, str(topic_id),
             "id must be lowercase letters, digits and underscores, starting with a letter")
    if expected_id is not None:
        _require(topic_id == expected_id, topic_id, f"id does not match the file name {expected_id!r}")

    color = _str(raw, "accent_color", topic_id)
    _require(_COLOR_RE.match(color) is not None, topic_id, "accent_color must look like #c8b89a")

    languages = raw.get("languages")
    _require(isinstance(languages, list) and languages and set(languages) <= KNOWN_LANGUAGES
             and len(set(languages)) == len(languages), topic_id, f"languages must be a non-empty subset of {sorted(KNOWN_LANGUAGES)}")

    variants = raw.get("variants")
    _require(isinstance(variants, list) and variants and set(variants) <= set(KNOWN_VARIANTS)
             and len(set(variants)) == len(variants), topic_id, f"variants must be a non-empty subset of {sorted(KNOWN_VARIANTS)}")
    _require("0_0" in variants, topic_id, "variants must include 0_0 (the default variant)")
    if "fr" not in languages:
        _require(not any(KNOWN_VARIANTS[v]["include_french"] for v in variants), topic_id,
                 "a variant includes French but languages does not")

    spotlight = raw.get("spotlight")
    _require(spotlight in KNOWN_SPOTLIGHTS, topic_id, f"spotlight must be one of {sorted(KNOWN_SPOTLIGHTS)}")

    max_articles = raw.get("max_articles", DEFAULT_MAX_ARTICLES)
    _require(isinstance(max_articles, int) and not isinstance(max_articles, bool) and max_articles >= 0, topic_id,
             "max_articles must be a non-negative integer (0 = no cap)")

    raw_cats = raw.get("categories")
    _require(isinstance(raw_cats, list) and 1 <= len(raw_cats) <= MAX_CATEGORIES, topic_id,
             f"categories must be a list of 1 to {MAX_CATEGORIES}")
    categories = tuple(_parse_category(c, topic_id) for c in raw_cats)
    names = [c.name for c in categories]
    _require(len(set(names)) == len(names), topic_id, "category names must be unique")
    for c in categories:
        if c.variant_flag:
            _require(any(KNOWN_VARIANTS[v][c.variant_flag] for v in variants) and
                     any(not KNOWN_VARIANTS[v][c.variant_flag] for v in variants), topic_id,
                     f"category {c.name!r} is tied to {c.variant_flag} but the variants never differ on it")

    raw_sources = raw.get("sources")
    _require(isinstance(raw_sources, list) and raw_sources, topic_id, "sources must be a non-empty list")
    sources = tuple(_parse_source(s, topic_id) for s in raw_sources)

    paywalled = raw.get("paywalled_domains") or []
    _require(isinstance(paywalled, list) and all(isinstance(d, str) and d for d in paywalled), topic_id,
             "paywalled_domains must be a list of domains")

    prompts = raw.get("prompts") or {}
    _require(isinstance(prompts, dict) and set(prompts) <= set(PROMPT_KEYS), topic_id,
             f"prompts may only name {sorted(PROMPT_KEYS)}")
    for key in PROMPT_KEYS:
        path = prompts.get(key) or PROMPT_KEYS[key]
        _require(os.path.isfile(os.path.join(ROOT, path)), topic_id, f"prompt file {path!r} does not exist")

    guidelines = raw.get("guidelines") or ""
    disclaimer = raw.get("disclaimer") or ""
    _require(isinstance(guidelines, str) and isinstance(disclaimer, str), topic_id,
             "guidelines and disclaimer must be text")

    return Topic(
        id=topic_id,
        name=_str(raw, "name", topic_id),
        tagline=_str(raw, "tagline", topic_id),
        audience=_str(raw, "audience", topic_id),
        accent_color=color,
        languages=tuple(languages),
        variants=tuple(variants),
        spotlight=spotlight,
        max_articles=max_articles,
        categories=categories,
        sources=sources,
        paywalled_domains=tuple(paywalled),
        prompts=dict(prompts),
        guidelines=guidelines.strip(),
        disclaimer=disclaimer.strip(),
    )


def list_topic_ids() -> list[str]:
    if not os.path.isdir(TOPICS_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(TOPICS_DIR) if f.endswith(".yaml"))


_CACHE: dict[str, Topic] = {}


def get_topic(topic_id: str | None = None) -> Topic:
    """Load (and cache) one profile. None or empty means the default topic."""
    topic_id = (topic_id or DEFAULT_TOPIC).strip().lower()
    if topic_id not in _CACHE:
        if _ID_RE.match(topic_id) is None:
            raise TopicError(f"unknown topic {topic_id!r}")
        path = os.path.join(TOPICS_DIR, f"{topic_id}.yaml")
        if not os.path.isfile(path):
            raise TopicError(f"unknown topic {topic_id!r}")
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        _CACHE[topic_id] = parse_topic(raw, expected_id=topic_id)
    return _CACHE[topic_id]


def is_valid_topic(topic_id: str | None) -> bool:
    try:
        get_topic(topic_id)
        return True
    except TopicError:
        return False


def topic_id_of(doc: dict | None) -> str:
    """The topic of a pipeline_runs doc; a doc with no `topic` (every run before topics existed) is "ai"."""
    value = (doc or {}).get("topic")
    return value if isinstance(value, str) and value else DEFAULT_TOPIC


def is_default_topic(doc: dict | None) -> bool:
    return topic_id_of(doc) == DEFAULT_TOPIC


def check_run_request(topic: str | None, kind: str | None) -> str | None:
    """Why a (topic, kind) request must be refused, or None when it is fine. Any known topic may be a release
    run: the owner's Scheduler jobs decide which topics run weekly, agents/launch.py decides who can subscribe."""
    if topic is None or topic == "":
        return None
    if not is_valid_topic(topic):
        return f"Unknown topic '{topic}'. Valid values: {', '.join(list_topic_ids())}."
    return None


def topic_for_run(run_id: str, use_firestore: bool | None = None) -> Topic:
    """The topic a run belongs to. Cloud mode reads it from the run doc; local mode reads env TOPIC."""
    if use_firestore is None:
        from config import USE_FIRESTORE
        use_firestore = USE_FIRESTORE
    if not use_firestore:
        return get_topic(os.environ.get("TOPIC"))
    from google.cloud import firestore
    from config import FIRESTORE_COLLECTION, GCP_PROJECT_ID
    snap = firestore.Client(project=GCP_PROJECT_ID).collection(FIRESTORE_COLLECTION).document(run_id).get()
    return get_topic(topic_id_of(snap.to_dict()))
