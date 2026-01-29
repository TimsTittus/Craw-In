import hashlib
from elasticsearch import Elasticsearch

class ESPipeline:
    def __init__(self): self.es = Elasticsearch('http://elasticsearch:9200')
    def process_item(self, item, spider):
        idx = 'news_idx' if spider.name == 'news' else 'web_idx'
        doc_id = hashlib.sha256(item['url'].encode()).hexdigest()
        self.es.index(index=idx, id=doc_id, document=item)
        print(f'Indexed: {item["title"]}')
        return item
