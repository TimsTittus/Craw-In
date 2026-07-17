# backend/ingestion.py
"""Utility that bridges the crawler output to both indexing engines.

Provides an async ``ingest_urls`` function that:
1. Fetches each URL via ``ConcurrentIngestionEngine``.
2. Extracts readable text with trafilatura.
3. Stores the raw text in the keyword store.
4. Generates a sentence‑embedding using FastEmbed.
5. Upserts the vector into Qdrant.
"""

import asyncio
from typing import List
import trafilatura

from .crawler import ConcurrentIngestionEngine
from .search import SearchIndexingFabric, get_keyword_engine, add_keyword_document, KeywordDocument
from fastembed import TextEmbedding
from .plugins.playwright_ingestion_plugin import PlaywrightIngestionPlugin
from .utils.concurrency import limited_playwright_scrape
from .config import PLAYWRIGHT_TIMEOUT

# FastEmbed instance (uses the default ``sentence-transformers/all-MiniLM-L6-v2`` model)
embedder = TextEmbedding()

async def ingest_urls(urls: List[str], collection_name: str = "documents") -> None:
    """Ingest a list of URLs into both the keyword and semantic stores.

    Parameters
    ----------
    urls: List[str]
        URLs to fetch and index.
    collection_name: str, optional
        Target Qdrant collection name. Defaults to ``"documents"``.
    """
    # Initialise crawl engine and Qdrant client
    crawler = ConcurrentIngestionEngine()
    fabric = SearchIndexingFabric()
    fabric.initialize_collection(collection_name, dim=384) 

    kw_session = get_keyword_engine()
    
    existing_count = kw_session.query(KeywordDocument).count()

    for idx, url in enumerate(urls, start=existing_count + 1):
        result = await crawler.process_url(url)
        if result["status"] != 200:
            continue
        text = result.get("content", "") or ""
        source = result.get("source", "unknown")
        # Store in keyword DB
        add_keyword_document(kw_session, title=url, content=text, source=source)
        # Compute embedding and upsert into Qdrant
        vector = list(embedder.embed([text]))[0].tolist()
        fabric.index_document(collection_name, doc_id=idx, vector=vector, payload={"url": url})