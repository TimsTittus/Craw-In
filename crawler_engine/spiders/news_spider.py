from scrapy_redis.spiders import RedisSpider
from scrapy.spiders import Rule
from scrapy.linkextractors import LinkExtractor

class NewsSpider(RedisSpider):
    name = 'news_spider'
    # This spider listens to a DIFFERENT queue
    redis_key = 'news:start_urls'

    def parse(self, response):
        # 1. VISUALIZATION
        self.logger.info(f"📰 News Found: {response.url}")

        # 2. EXTRACT DATA (Enhanced for News)
        yield {
            'url': response.url,
            'title': response.css('title::text').get(),
            'content': response.css('p::text').getall(), # Get all paragraph text
            'timestamp': 'now',
            'type': 'news' # Tag it specifically
        }

        # 3. FOLLOW LINKS (Restricted)
        # Only follow links that point to the SAME domain (allow_domains=...)
        # We filter for links that look like articles (contain year/month or 'article')
        # Note: For a dynamic generic crawler, we simply restrict to the allowed domain.
        domain = response.url.split("/")[2]
        links = LinkExtractor(allow_domains=[domain]).extract_links(response)

        for link in links:
            yield response.follow(link, self.parse)