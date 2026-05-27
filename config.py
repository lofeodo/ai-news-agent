# config.py

# ArXiv fetching
MAX_FETCH = 500         # safety ceiling for ArXiv API
SAMPLE_SIZE = 50        # papers to randomly sample for scoring per day
LOOKBACK_HOURS = 24     # how far back to fetch papers

# Claude
SCORING_MODEL = "claude-haiku-4-5-2025-1001"
MAX_TOKENS = 1000

# Paths
DATA_DIR = "data"
