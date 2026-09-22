MAX_ROUNDS = 3
MAX_CONTEXT_CHARS = 6000
RUNS_DIR = "runs"

# Firecrawl web research for the researcher agent (see research.py).
FIRECRAWL_MAX_URLS = 2  # how many URLs found in a step prompt get scraped
FIRECRAWL_SEARCH_LIMIT = 3  # results fetched when the step has no URL
FIRECRAWL_MAX_CHARS = 6000  # total chars of scraped markdown added to the prompt
FIRECRAWL_TIMEOUT = 60.0  # seconds per Firecrawl request

