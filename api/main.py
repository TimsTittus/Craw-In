from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException
from elasticsearch import Elasticsearch
from pydantic import BaseModel
import redis

app = FastAPI(title="OmniCrawler Search Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- CONNECTIONS ---
# Connect to the Brain (Elasticsearch)
es = Elasticsearch("http://localhost:9200")
# Connect to the Spider Manager (Redis)
r = redis.Redis(host='localhost', port=6379, decode_responses=True)

# --- DATA MODELS ---
class CrawlRequest(BaseModel):
    url: str
    module: str = "general"  # Options: "general", "news"

# --- ENDPOINTS ---

@app.get("/")
def read_root():
    return {"status": "Online", "modules": ["general", "news"]}

@app.get("/search")
def search(q: str, module: str = "general"):
    """
    The Universal Search Interface.
    Current Modules:
    - general: Searches the broad web index.
    - news: (Placeholder) Will search the news index.
    """

    # 1. Select the correct Index based on the module
    target_index = "web_search"
    if module == "news":
        target_index = "news_search" # We will build this in Phase 4

    # 2. Construct the Query
    # This is a 'multi_match' query - it looks in title AND content.
    query_body = {
        "query": {
            "multi_match": {
                "query": q,
                "fields": ["title", "content"] 
            }
        }
    }

    # 3. Execute Search
    try:
        res = es.search(index=target_index, body=query_body)
        hits = res['hits']['hits']

        # Clean up results for the user
        results = []
        for hit in hits:
            results.append({
                "title": hit['_source'].get('title'),
                "url": hit['_source'].get('url'),
                "score": hit['_score']
            })
        return {"count": len(results), "results": results}

    except Exception as e:
        # Handle case where index doesn't exist yet
        return {"error": str(e), "suggestion": "Try crawling some data first!"}

@app.post("/crawl")
def trigger_crawl(request: CrawlRequest):
    """
    Smart Dispatcher: Sends the URL to the correct Spider Queue.
    """
    if request.module == "news":
        # Push to the News Spider's queue
        r.lpush('news:start_urls', request.url)
        queue_name = "news:start_urls"
    else:
        # Push to the General Spider's queue
        r.lpush('master:start_urls', request.url)
        queue_name = "master:start_urls"

    return {
        "message": f"Spider dispatched to {request.url}", 
        "module": request.module,
        "queue": queue_name
    }
