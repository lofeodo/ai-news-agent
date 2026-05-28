# config.py

# ArXiv fetching
MAX_FETCH = 500         # safety ceiling for ArXiv API
SAMPLE_SIZE = 35        # papers to randomly sample for scoring per day
LOOKBACK_HOURS = 24     # how far back to fetch papers
WORD_CUTOFF = 2000

# Paper scoring
MAX_SCORE = 28

# News fetching
NEWS_FETCH_SIZE = 10    # articles per source

# Claude
SCORING_MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1000

# Paths
DATA_DIR = "data"