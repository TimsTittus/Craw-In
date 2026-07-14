import asyncio
from playwright.async_api import async_playwright

class PlaywrightIngestionPlugin:
    """Fallback scraper that renders JavaScript with Playwright.

    It opens the page, waits for network to be idle, and extracts the visible text.
    """
    def __init__(self, timeout: int = 20):
        self.timeout = timeout  # seconds

    async def scrape(self, url: str) -> str:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)
                # Extract full visible text of the page
                content = await page.evaluate("() => document.body.innerText")
                await browser.close()
                return content.strip() if content else ""
        except Exception as e:
            # If the browser executable is missing, attempt to install browsers automatically
            import subprocess, sys, os
            if "Executable doesn't exist" in str(e):
                print("[PlaywrightIngestionPlugin] browsers missing, installing...", flush=True)
                try:
                    subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], check=True)
                    # Retry after installation
                    return await self.scrape(url)
                except Exception as install_err:
                    print(f"[PlaywrightIngestionPlugin] failed to install browsers: {install_err}", flush=True)
            print(f"[PlaywrightIngestionPlugin] error scraping {url}: {e}", flush=True)
            return ""