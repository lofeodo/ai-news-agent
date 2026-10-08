# config.py

import os
from datetime import datetime, timedelta, timezone

# ArXiv fetching
MAX_FETCH = 500              # safety ceiling for ArXiv API
SAMPLE_SIZE = 35             # papers to score per week
WORD_CUTOFF = 5000           # papers — covers method + results, excludes references (~6-7k tokens)
PAPERS_IN_NEWSLETTER = 1     # how many top-scored papers appear in the newsletter (one spotlight per week)
ARTICLE_WORD_LIMIT = 1500 # news articles — most articles are under this anyway

# Trending-paper shortlist (agent1a): the SAMPLE_SIZE most-upvoted Hugging Face Daily Papers of the
# last TRENDING_LOOKBACK_DAYS that have not been spotlighted before are scored by Claude.
TRENDING_LOOKBACK_DAYS = 14
HF_DAILY_PAPERS_URL = "https://huggingface.co/api/daily_papers"

# Paper scoring: 8 dimensions scored by Claude (max 33) + community traction computed in code
# from Hugging Face upvotes (1..TRACTION_MAX_POINTS, a percentile within the shortlist).
CLAUDE_MAX_SCORE = 33
TRACTION_MAX_POINTS = 8
MAX_SCORE = CLAUDE_MAX_SCORE + TRACTION_MAX_POINTS

# Claude
SCORING_MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1000           # used for scoring
FILTER_MAX_TOKENS = 4000    # used for news filtering — up to 100 index+category pairs per batch
PAPER_SUMMARY_MAX_TOKENS = 110   # 2-3 sentence hook (~35 words); a cut-off reply is trimmed to its last full sentence
NEWS_SUMMARY_MAX_TOKENS  = 200   # 2-3 sentences

# Hard cap on articles per newsletter section, enforced in code by agent3
SECTION_CAP_DEFAULT = 3
SECTION_CAPS = {
    "Model & Product Releases": 4,
    "Open Source & Tools": 4,
}


def section_cap(category: str) -> int:
    return SECTION_CAPS.get(category, SECTION_CAP_DEFAULT)


# Ranked runners-up kept per section and selection pass, for the dedup loop's fallbacks
RUNNERS_UP_MAX = 6

# Duplicate detection in agent3 (agents/dedup.py); Haiku until the dedup eval picks a winner
DEDUP_MODEL = SCORING_MODEL
DEDUP_MAX_TOKENS = 1000
# agent3 dedup loop (agents/agent3_dedup_graph.py): "graph" (default) or "off" (no-redeploy rollback)
AGENT3_DEDUP_MODE = os.environ.get("AGENT3_DEDUP_MODE", "graph").strip().lower()
if AGENT3_DEDUP_MODE not in ("graph", "off"):
    raise ValueError(f"AGENT3_DEDUP_MODE must be 'graph' or 'off', got {AGENT3_DEDUP_MODE!r}")
try:
    DEDUP_MAX_ITERATIONS = max(1, int(os.environ.get("DEDUP_MAX_ITERATIONS", RUNNERS_UP_MAX)))  # fallback checks per section
except ValueError:
    DEDUP_MAX_ITERATIONS = RUNNERS_UP_MAX

# Shared timing
LOOKBACK_HOURS = 168    # 7 days — applies to both ArXiv and news fetching

# News fetching
NEWS_FETCH_SIZE = 100   # articles per NewsAPI query (NewsAPI hard cap)

NEWSAPI_QUERIES = [
    # English global
    '"artificial intelligence" OR "machine learning" OR "deep learning"',
    '"LLM" OR "large language model" OR "generative AI" OR "foundation model"',
    '"OpenAI" OR "Anthropic" OR "Google DeepMind" OR "Mistral" OR "xAI" OR "Meta AI" OR "Apple Intelligence" OR "Amazon Bedrock" OR "Cohere" OR "Stability AI" OR "Midjourney" OR "Perplexity"',
    '"ChatGPT" OR "Claude" OR "Gemini" OR "Llama" OR "Grok" OR "Copilot" OR "Sora" OR "DALL-E" OR "Fable"',
    '"AI regulation" OR "AI safety" OR "AI policy" OR "AI law" OR "AI ethics" OR "AI governance" OR "AI alignment"',
    '"open source AI" OR "AI tools" OR "AI agent" OR "AI assistant" OR "agentic AI"',
    '"AI chip" OR "GPU" OR "NVIDIA" OR "semiconductor" OR "AI infrastructure" OR "AI hardware"',
    '"AI research" OR "neural network" OR "transformer model" OR "diffusion model" OR "reinforcement learning"',
    # French global
    '"intelligence artificielle" OR "apprentissage automatique" OR "apprentissage profond" OR "IA générative" OR "grand modèle de langage"',
    # Canada / Montreal (English + French)
    '("AI" OR "artificial intelligence" OR "intelligence artificielle" OR "IA") AND ("Canada" OR "Montreal" OR "Montréal" OR "Quebec" OR "Québec" OR "Toronto" OR "Ottawa")',
]

PAYWALLED_DOMAINS = [
    "theglobeandmail.com",
    "nytimes.com",
    "ft.com",
    "wsj.com",
    "bloomberg.com",
    "theathletic.com",
    "thetimes.co.uk",
    "economist.com",
    "washingtonpost.com",
    "wired.com",
    "telegraph.co.uk",
    "consent.yahoo.com",    # Yahoo consent redirect — no article content
    "pypi.org",             # Python package index — version bumps, not news
]

ALLOWED_LANGUAGES = {"en", "fr"}

# Unicode ranges for non-Latin scripts — titles containing these are dropped
NON_LATIN_RANGES = [
    (0x0400, 0x04FF),   # Cyrillic
    (0x0600, 0x06FF),   # Arabic
    (0x0900, 0x097F),   # Devanagari
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs (Chinese/Japanese)
    (0x3040, 0x30FF),   # Hiragana + Katakana
    (0xAC00, 0xD7AF),   # Korean Hangul
    (0x0E00, 0x0E7F),   # Thai
    (0x0590, 0x05FF),   # Hebrew
]

# Paths
DATA_DIR = "data"

# GCP
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")

# Pub/Sub topic names
TOPIC_PIPELINE_START     = "pipeline-start"
TOPIC_PAPERS_SCORED      = "papers-scored"
TOPIC_NEWS_FILTERED      = "news-filtered"
TOPIC_CONTENT_SUMMARIZED = "content-summarized"

# Firestore
FIRESTORE_COLLECTION = "pipeline_runs"
USE_FIRESTORE        = os.environ.get("USE_FIRESTORE", "false").lower() == "true"
SUBSCRIBERS_COLLECTION = "subscribers"

# Subscriber cap
MAX_SUBSCRIBERS = int(os.environ.get("MAX_SUBSCRIBERS", "50000"))

# Health check alerting — where agent_healthcheck sends a problem report.
# Never used for subscriber-facing sends.
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")

# Drift monitoring (agents/drift.py). A metric is flagged only if its test is
# significant AND the effect clears a floor: with ~500 articles a week, tiny shifts are
# significant but not interesting, and weekly cadence means false alarms are costly.
DRIFT_BASELINE_RUNS = 4        # prior weeks pooled as the baseline
DRIFT_MIN_BASELINE_RUNS = 3    # fewer usable prior runs -> "insufficient history"
DRIFT_P_THRESHOLD = 0.01
DRIFT_KS_MIN_D = 0.15          # KS statistic on the confidence distribution
DRIFT_MIN_SHARE_SHIFT = 0.10   # largest per-category share change (fraction of articles)
DRIFT_MIN_RATE_SHIFT = 0.10    # review-rate change (fraction of articles)


def parse_started_at(raw: str) -> datetime:
    """Parse a pipeline_runs `started_at` string to an aware UTC datetime.

    Not every writer includes an offset: orchestrator.py uses
    datetime.now(timezone.utc) (aware), but agent1a_fetch_papers.py used to
    write datetime.now().isoformat() (naive) — and old Firestore docs still
    have that shape. A naive value is assumed to be UTC (Cloud Run containers
    run in UTC). Shared here so every consumer (agent_healthcheck.py,
    agent4_send.py, ...) stays consistent rather than each reimplementing
    this and risking one of them drifting out of tolerance again.
    """
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
# Token / cost drift (agents/drift.py usage_ratio_test). Four prior weeks are too few for p-values,
# so a metric is flagged when it moves at least MIN_RATIO from the prior-run median AND the absolute
# change clears a floor (so a tiny agent or a cheap week can't trip it).
DRIFT_USAGE_MIN_RATIO  = 0.5     # +/-50% versus the median of prior runs
DRIFT_USAGE_MIN_TOKENS = 100_000 # absolute token change floor
DRIFT_USAGE_MIN_USD    = 0.10    # absolute cost change floor

# Online summary judge (agents/judge.py, agents/online_judge.py). The judge must be a stronger
# model than the Haiku that writes the summaries; it is same-family, so its bias is a documented limit.
JUDGE_MODEL = "claude-sonnet-5-5"
JUDGE_MAX_TOKENS = 2000           # claude-sonnet-5-5 emits a thinking block that counts against this; 600 truncated ~1 in 8 replies
JUDGE_MAX_ITEMS = 12            # summaries judged per weekly run (all papers first, then seeded news sample)
JUDGE_MAX_SOURCE_WORDS = 5000   # source text sent per item, matches WORD_CUTOFF
JUDGE_MAX_USD = 0.40            # hard weekly cap, checked on an estimate before any call (worst case: 12 items of 5000 words)

# Judge alerting. Until the calibration against the repo owner's 40 labels (evals/run_judge_calibration.py)
# shows acceptable agreement, the weekly judge only reports: a flagged quality drop does not mark the
# pipeline as "problem detected". Flip to True only after reading evals/results/judge_calibration.json.
JUDGE_ALERTING_ENABLED = False
JUDGE_MIN_UNSUPPORTED_SHIFT = 0.15   # unsupported-rate rise vs the prior weeks' pooled rate (fraction of items)

# SendGrid click tracking (agents/sendgrid_webhook.py, agents/agent_subscriptions.py /sendgrid/events).
# Signed webhook calls older (or newer) than this are rejected, which bounds replay of a captured request.
SENDGRID_WEBHOOK_TOLERANCE_SECONDS = 600
# Lower-case substrings of user agents counted as automated (link scanners and fetchers). A partial
# filter only: corporate scanners also use ordinary browser agents, which is what the early-click bucket is for.
CLICK_BOT_UA_MARKERS = ("bot", "spider", "crawler", "scanner", "preview", "proofpoint", "barracuda",
                        "mimecast", "safelinks", "python-requests", "curl/", "wget", "headlesschrome")
# Clicks within this many seconds of agent4 starting its send are counted separately ("early"): mail
# security scanners prefetch every link at delivery time, long before a person reads the email.
CLICK_EARLY_SECONDS = 300

# agent4: turn on SendGrid click tracking and tag each email with the pipeline run id, so the Event Webhook
# can count clicks per article. Unset = the newsletter is sent exactly as before (the rollback switch).
CLICK_TRACKING = os.environ.get("CLICK_TRACKING", "false").lower() == "true"


try:
    from zoneinfo import ZoneInfo
    NEWSLETTER_TZ = ZoneInfo("America/Toronto")
except Exception:  # pragma: no cover - zoneinfo/tzdata missing
    NEWSLETTER_TZ = timezone(timedelta(hours=-5))  # fallback: fixed EST offset


def newsletter_send_date(now: datetime | None = None) -> datetime:
    """The date the newsletter is (or will be) sent, as a Toronto-local datetime.

    The pipeline drafts on Sunday at noon and agent4 sends on Monday at 7 AM, so a
    run composed on a Sunday is dated the next day. Any other day (Monday, or a
    manual recovery run later in the week) is dated today.
    """
    local = (now or datetime.now(timezone.utc)).astimezone(NEWSLETTER_TZ)
    if local.weekday() == 6:  # Sunday
        local = local.replace(hour=12) + timedelta(days=1)
    return local
