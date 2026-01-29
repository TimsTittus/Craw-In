# Minimal settings required for Scrapy startup
BOT_NAME = 'omni'
SPIDER_MODULES = ['worker']
NEWSPIDER_MODULE = 'worker'
ROBOTSTXT_OBEY = True

# Redis settings
REDIS_URL = 'redis://redis:6379'

# Scrapy-Redis scheduler
SCHEDULER = 'scrapy_redis.scheduler.Scheduler'
DUPEFILTER_CLASS = 'scrapy_redis.dupefilter.RFPDupeFilter'
SCHEDULER_PERSIST = True

# Item pipeline
ITEM_PIPELINES = {
    'pipelines.ESPipeline': 300
}

# Don't clean up Redis queues
SCHEDULER_QUEUE_CLASS = 'scrapy_redis.queue.SpiderPriorityQueue'
