import asyncio
import httpx
import trafilatura
from .plugins.playwright_ingestion_plugin import PlaywrightIngestionPlugin
from .utils.concurrency import limited_playwright_scrape
from .config import PLAYWRIGHT_TIMEOUT
from typing import Dict

import logging

logger = logging.getLogger(__name__)

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
            try:
                async with httpx.AsyncClient(headers=self.headers, timeout=10.0) as client:
                    response = await client.get(url, follow_redirects=True)
                    if response.status_code == 200:
                        downloaded = response.text
                        extracted_text = trafilatura.extract(
                            downloaded,
                            output_format="markdown",
                            include_links=True,
                            include_formatting=True
                        )
                        source = "trafilatura"
                        
                        if extracted_text and len(extracted_text) < 800:
                            import markdownify  # type: ignore
                            full_md = markdownify.markdownify(downloaded, heading_style="ATX").strip()
                            if len(full_md) > len(extracted_text) * 1.5:
                                extracted_text = full_md
                                source = "markdownify"
                                print(f"[Craw-In Engine] trafilatura output too short ({len(full_md)} chars). Switched to raw markdownify.", flush=True)

                        if not extracted_text or len(extracted_text) < 200:
                            print(f"[Craw-In Engine] Content too short ({len(extracted_text) if extracted_text else 0} chars). Switching to Playwright fallback for {url}...", flush=True)
                            logger.info(f"Content too short for {url}. Invoking Playwright fallback...")
                            playwright_plugin = PlaywrightIngestionPlugin(timeout=PLAYWRIGHT_TIMEOUT)
                            extracted_text = await limited_playwright_scrape(playwright_plugin, url) or ""
                            source = "playwright"
                            print(f"[Craw-In Engine] Playwright fallback completed for {url}. Extracted {len(extracted_text)} bytes.", flush=True)
                            logger.info(f"Playwright fallback completed for {url}. Extracted {len(extracted_text)} characters.")
                            
                        return {"url": url, "content": extracted_text, "status": 200, "source": source}
                    # Non‑200 responses – graceful fallback
                    return {"url": url, "content": "", "status": response.status_code, "source": "none"}
            except httpx.RequestError as e:
                # Catch DNS failures, connection timeouts, etc.
                return {"url": url, "content": "", "status": 0, "source": "none"}
            except Exception as e:
                # Invalid URLs, extraction failures, etc. – treat as unreachable
                logger.warning(f"Failed to process {url}: {e}")
                return {"url": url, "content": "", "status": 0, "source": "none"}