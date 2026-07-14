import os

# Playwright configuration defaults
PLAYWRIGHT_TIMEOUT = int(os.getenv("PLAYWRIGHT_TIMEOUT", "20"))  # seconds, max wait for networkidle
PLAYWRIGHT_CONCURRENCY = int(os.getenv("PLAYWRIGHT_CONCURRENCY", "3"))  # max simultaneous browsers