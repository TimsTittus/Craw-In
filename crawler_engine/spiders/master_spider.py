from scrapy_redis.spiders import RedisSpider

class MasterSpider(RedisSpider):
    name = 'master'
    # This spider will look for a list named 'master:start_urls' in Redis

    def parse(self, response):
        # 1. VISUALIZATION LOG
        self.logger.info(f"Crawled: {response.url}")

        # 2. EXTRACT DATA (Simplified for Phase 1)
        yield {
            'url': response.url,
            'title': response.css('title::text').get(),
            'status': response.status,
        }

        # 3. FOLLOW LINKS
        # distinct() to avoid duplicates on the same page
        # Filtering binary extensions to save bandwidth
        ignored_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.pdf', '.zip', '.rar', '.mp4', '.mp3', '.exe')
        
        for href in response.css('a::attr(href)'):
            url_str = href.get()
            if not url_str:
                continue
            
            # Simple check to avoid downloading binaries
            clean_url = url_str.split('?')[0].lower()
            if clean_url.endswith(ignored_extensions):
                continue

            yield response.follow(href, self.parse)