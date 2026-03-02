import os

# Claude API
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
MODEL_ID = "claude-haiku-4-5-20251001"

# Batching
LLM_BATCH_SIZE = 20
MAX_LOOKUP_NAMES_PER_CALL = 200

# Retry
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0
REQUEST_TIMEOUT = 60

# Matching
EXACT_MATCH_CONFIDENCE = 1.0

# Output columns
OUTPUT_COLUMNS = [
    "Unique ID",
    "Supplier Name",
    "Matched Supplier Name",
    "Supplier Code",
    "Match Method",
    "Confidence",
]
