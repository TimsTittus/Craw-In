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

# 16 concurrent requests is a safe starting point for a student laptop
# Increased for efficiency
CONCURRENT_REQUESTS = 32

# Add a delay to avoid looking like a DDoS attack
DOWNLOAD_DELAY = 1 
# Disable cookies (unless needed) to save bandwidth
COOKIES_ENABLED = False

# Reduce log verbosity to improve I/O performance
LOG_LEVEL = 'INFO'

# Identify yourself (Good practice for cybersecurity/ethical crawling)
USER_AGENT = 'ResearchBot-StudentProject (+http://www.yourdomain.com)'

# Disable Telnet Console (Security)
TELNETCONSOLE_ENABLED = False

ITEM_PIPELINES = {
   "crawler_engine.pipelines.ElasticsearchPipeline": 300,
}