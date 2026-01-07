import hashlib
from elasticsearch import Elasticsearch, helpers

class ElasticsearchPipeline:
    def __init__(self):
        self.es = Elasticsearch("http://localhost:9200")
        self.items_buffer = []
        self.buffer_size = 100 # Adjustable batch size

    def process_item(self, item, spider):
        # 1. Generate ID
        doc_id = hashlib.sha256(item['url'].encode('utf-8')).hexdigest()

        # 2. Decide Destination Index based on Spider Name
        if spider.name == 'news_spider':
            target_index = "news_search"
            # For news, we might want to join the paragraph list into a single string
            if isinstance(item.get('content'), list):
                item['content'] = " ".join(item['content'])
        else:
            target_index = "web_search"

        # 3. Define Document Action for Bulk
        document = {
            '_index': target_index,
            '_id': doc_id,
            '_source': {
                'url': item['url'],
                'title': item.get('title'),
                'content': item.get('content'), # Can be raw text or list
                'timestamp': 'now',
                'module': spider.name
            }
        }

        # 4. Add to Buffer
        self.items_buffer.append(document)

        # 5. Flush if buffer full
        if len(self.items_buffer) >= self.buffer_size:
            self._flush()

        return item

    def close_spider(self, spider):
        # Flush remaining items when spider closes
        self._flush()

    def _flush(self):
        if self.items_buffer:
            try:
                helpers.bulk(self.es, self.items_buffer)
                print(f"✅ Bulk Indexed {len(self.items_buffer)} items")
            except Exception as e:
                print(f"❌ Error indexing batch: {e}")
            finally:
                self.items_buffer = []