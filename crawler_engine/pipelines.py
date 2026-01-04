import hashlib
from elasticsearch import Elasticsearch

class ElasticsearchPipeline:
    def __init__(self):
        # Connect to our local database
        self.es = Elasticsearch("http://localhost:9200")

    def process_item(self, item, spider):
        # 1. Create a unique ID for the page (using URL hash)
        # This prevents duplicate entries if we crawl the same page twice.
        doc_id = hashlib.sha256(item['url'].encode('utf-8')).hexdigest()

        # 2. Define the document structure
        document = {
            'url': item['url'],
            'title': item.get('title'),
            'content': item.get('status'), # Storing status for now
            'timestamp': 'now' # Elastic will auto-date this
        }

        # 3. Index the document (Upsert: Update if exists, Insert if new)
        self.es.index(index="web_search", id=doc_id, document=document)

        # Print to console so you know it worked
        print(f"✅ Indexed: {item['url']}")

        return item