import multiprocessing
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from scrapy_redis.spiders import RedisSpider

# --- SPIDERS ---
class GeneralSpider(RedisSpider):
    name = 'general'; redis_key = 'general_queue'
    def parse(self, response):
        yield {'url': response.url, 'title': response.css('title::text').get(), 'content': ' '.join(response.css('p::text').getall())}
        for href in response.css('a::attr(href)'):
            yield response.follow(href, self.parse)

class NewsSpider(RedisSpider):
    name = 'news'; redis_key = 'news_queue'
    def parse(self, response):
        yield {'url': response.url, 'title': response.css('title::text').get(), 'content': ' '.join(response.css('p::text').getall())}

# --- LAUNCHER ---
settings = get_project_settings()

def run_spider(spider_cls):
    p = CrawlerProcess(settings)
    p.crawl(spider_cls)
    p.start(stop_after_crawl=False)

if __name__ == '__main__':
    p1 = multiprocessing.Process(target=run_spider, args=(GeneralSpider,))
    p2 = multiprocessing.Process(target=run_spider, args=(NewsSpider,))
    p1.start(); p2.start()
    p1.join(); p2.join()
