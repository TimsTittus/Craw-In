import hashlib
from elasticsearch import Elasticsearch

class ElasticsearchPipeline:
    def __init__(self):
        self.es = Elasticsearch("http://localhost:9200")

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

        # 3. Define Document
        document = {
            'url': item['url'],
            'title': item.get('title'),
            'content': item.get('content'), # Can be raw text or list
            'timestamp': 'now',
            'module': spider.name
        }

        # 4. Save to Database
        self.es.index(index=target_index, id=doc_id, document=document)
        print(f"✅ Indexed in [{target_index}]: {item['title']}")

        return item