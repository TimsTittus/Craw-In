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

from .crawler import ConcurrentIngestionEngine
from .search import SearchIndexingFabric, get_keyword_engine, add_keyword_document
from fastembed import TextEmbedding

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
    fabric.initialize_collection(collection_name, dim=384)  # 384 matches FastEmbed output dim

    # Initialise keyword DB session
    kw_session = get_keyword_engine()

    for idx, url in enumerate(urls, start=1):
        result = await crawler.process_url(url)
        if result["status"] != 200:
            # Skip non‑200 responses; you can log or handle as needed
            continue
        text = result.get("content", "") or ""
        # Store in keyword DB
        add_keyword_document(kw_session, title=url, content=text)
        # Compute embedding and upsert into Qdrant
        vector = list(embedder.embed([text]))[0].tolist()
        fabric.index_document(collection_name, doc_id=idx, vector=vector, payload={"url": url})