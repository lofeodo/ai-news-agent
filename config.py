# config.py

import os

# ArXiv fetching
MAX_FETCH = 500         # safety ceiling for ArXiv API
SAMPLE_SIZE = 35        # papers to score per week (pre-filter replaces random sample later)
WORD_CUTOFF = 5000        # papers — covers method + results, excludes references (~6-7k tokens)
ARTICLE_WORD_LIMIT = 1500 # news articles — most articles are under this anyway

# Paper scoring
MAX_SCORE = 28

# Claude
SCORING_MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1000           # used for scoring
FILTER_MAX_TOKENS = 4000    # used for news filtering — up to 100 index+category pairs per batch
PAPER_SUMMARY_MAX_TOKENS = 600   # ~150 words with breathing room for 4 paragraphs
NEWS_SUMMARY_MAX_TOKENS  = 200   # 2-3 sentences

# Shared timing
LOOKBACK_HOURS = 168    # 7 days — applies to both ArXiv and news fetching

# News fetching
NEWS_FETCH_SIZE = 10   # articles per NewsAPI query

NEWSAPI_QUERIES = [
    # English global
    '"artificial intelligence" OR "machine learning" OR "deep learning"',
    '"LLM" OR "large language model" OR "generative AI" OR "foundation model"',
    '"OpenAI" OR "Anthropic" OR "Google DeepMind" OR "Mistral" OR "xAI" OR "Meta AI" OR "Apple Intelligence" OR "Amazon Bedrock" OR "Cohere" OR "Stability AI" OR "Midjourney" OR "Perplexity"',
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
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "ai-news-letter-497720")

# Pub/Sub topic names
TOPIC_PIPELINE_START     = "pipeline-start"
TOPIC_PAPERS_SCORED      = "papers-scored"
TOPIC_NEWS_FILTERED      = "news-filtered"
TOPIC_CONTENT_SUMMARIZED = "content-summarized"

# Firestore
FIRESTORE_COLLECTION = "pipeline_runs"
USE_FIRESTORE        = os.environ.get("USE_FIRESTORE", "false").lower() == "true"