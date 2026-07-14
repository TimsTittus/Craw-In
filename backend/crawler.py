import asyncio
import httpx
import trafilatura
from typing import Dict


class ConcurrentIngestionEngine:
    """Asynchronous crawler that fetches URLs concurrently, respects a
    configurable semaphore limit, and extracts readable markdown using
    *trafilatura*.

    Example usage::

        engine = ConcurrentIngestionEngine(concurrency_limit=5)
        result = await engine.process_url("https://example.com")
    """

    def __init__(self, concurrency_limit: int = 10):
        self.semaphore = asyncio.Semaphore(concurrency_limit)
        self.headers = {"User-Agent": "InfoOS-Crawler/2.0"}

    async def process_url(self, url: str) -> Dict[str, object]:
        """Fetch a single URL and return a dictionary with extracted content.

        Parameters
        ----------
        url: str
            The target URL to retrieve.

        Returns
        -------
        dict
            {
                "url": <original url>,
                "content": <markdown string (empty if extraction fails)>,
                "status": <HTTP status code>
            }
        """
        async with self.semaphore:
            async with httpx.AsyncClient(headers=self.headers, timeout=10.0) as client:
                response = await client.get(url, follow_redirects=True)
                if response.status_code == 200:
                    downloaded = response.text
                    # trafilatura returns ``None`` when it cannot extract text
                    extracted_text = trafilatura.extract(downloaded) or ""
                    return {"url": url, "content": extracted_text, "status": 200}
                # Non‑200 responses – graceful fallback
                return {"url": url, "content": "", "status": response.status_code}
