from fastapi import FastAPI
from elasticsearch import Elasticsearch
from pydantic import BaseModel
import redis

app = FastAPI()
es = Elasticsearch('http://elasticsearch:9200')
r = redis.Redis(host='redis', port=6379)

class R(BaseModel): url: str; module: str

@app.post('/crawl')
def crawl(req: R):
    queue = 'news_queue' if req.module == 'news' else 'general_queue'
    r.lpush(queue, req.url)
    return {'message': f'Dispatched to {queue}'}

@app.get('/search')
def search(q: str, module: str = 'general'):
    idx = 'news_idx' if module == 'news' else 'web_idx'
    try:
        res = es.search(index=idx, body={'query': {'multi_match': {'query': q, 'fields': ['title', 'content']}}})
        return {'results': [h['_source'] for h in res['hits']['hits']]}
    except: return {'results': []}
