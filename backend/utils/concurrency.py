import asyncio
from backend.config import PLAYWRIGHT_CONCURRENCY

playwright_semaphore = asyncio.Semaphore(PLAYWRIGHT_CONCURRENCY)

async def limited_playwright_scrape(plugin, url):
    async with playwright_semaphore:
        return await plugin.scrape(url)