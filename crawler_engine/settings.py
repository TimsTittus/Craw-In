BOT_NAME = "crawler_engine"
SPIDER_MODULES = ["crawler_engine.spiders"]
NEWSPIDER_MODULE = "crawler_engine.spiders"

# --- REDIS CONFIGURATION ( The Scalability Layer ) ---
# Enables scheduling storing requests queue in redis
SCHEDULER = "scrapy_redis.scheduler.Scheduler"
# Ensure all spiders share same duplicates filter through redis
DUPEFILTER_CLASS = "scrapy_redis.dupefilter.RFPDupeFilter"
# Don't cleanup redis queues, allows to pause/resume crawls
SCHEDULER_PERSIST = True
REDIS_URL = 'redis://localhost:6379'

# --- POLITENESS & SECURITY ( The Compliance Layer ) ---
ROBOTSTXT_OBEY = True

# Fix deprecation warning for request fingerprinting
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"

# 16 concurrent requests is a safe starting point for a student laptop
# Increased for efficiency
CONCURRENT_REQUESTS = 32

# Add a delay to avoid looking like a DDoS attack
DOWNLOAD_DELAY = 1 
# Disable cookies (unless needed) to save bandwidth
COOKIES_ENABLED = False

# Reduce log verbosity to improve I/O performance
LOG_LEVEL = 'INFO'

# Use a realistic browser User-Agent to avoid being blocked
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

# Disable Telnet Console (Security)
TELNETCONSOLE_ENABLED = False

# Allow spider to handle 403 responses instead of ignoring them
HTTPERROR_ALLOWED_CODES = [403]

ITEM_PIPELINES = {
   "crawler_engine.pipelines.ElasticsearchPipeline": 300,
}