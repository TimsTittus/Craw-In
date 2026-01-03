from scrapy_redis.spiders import RedisSpider

class MasterSpider(RedisSpider):
    name = 'master'
    # This spider will look for a list named 'master:start_urls' in Redis

    def parse(self, response):
        # 1. VISUALIZATION LOG (Just to see it working in terminal)
        self.logger.info(f"Crawled: {response.url}")

        # 2. EXTRACT DATA (Simplified for Phase 1)
        yield {
            'url': response.url,
            'title': response.css('title::text').get(),
            'status': response.status,
        }

        # 3. FOLLOW LINKS
        # This is a general crawler, so we follow all links. 
        # In Phase 4, we will restrict this based on features.
        for href in response.css('a::attr(href)'):
            yield response.follow(href, self.parse)