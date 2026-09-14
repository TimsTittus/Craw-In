import asyncio
from typing import Awaitable, Callable, Optional
from playwright.async_api import async_playwright  # type: ignore
from playwright.async_api import TimeoutError as PlaywrightTimeoutError  # type: ignore

UrlGuard = Callable[[str], Awaitable[bool]]

class PlaywrightIngestionPlugin:
    """Fallback scraper that renders JavaScript with Playwright.

    It opens the page, waits for network to be idle, and extracts the visible text.
    An optional ``url_guard`` (async predicate) blocks every browser request it rejects
    and discards pages whose final URL it rejects, e.g. to keep untrusted pages off
    internal networks.
    """
    def __init__(self, timeout: int = 20, url_guard: Optional[UrlGuard] = None):
        self.timeout = timeout  # seconds
        self.url_guard = url_guard

    async def _guard_route(self, route) -> None:
        if self.url_guard is not None and await self.url_guard(route.request.url):
            await route.continue_()
        else:
            await route.abort("blockedbyclient")

    async def scrape(self, url: str, _allow_install: bool = True) -> str:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    context = await browser.new_context()
                    if self.url_guard is not None:
                        await context.route("**/*", self._guard_route)
                    page = await context.new_page()
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)
                    except PlaywrightTimeoutError:
                        # Pages with long-polling/analytics never reach networkidle;
                        # use whatever has rendered if navigation itself succeeded.
                        if not page.url or page.url == "about:blank":
                            raise
                    if self.url_guard is not None and not await self.url_guard(page.url):
                        print(f"[PlaywrightIngestionPlugin] discarded {url}: final URL rejected by guard", flush=True)
                        return ""
                    # Extract full visible text of the page as HTML and convert to Markdown
                    content_html = await page.evaluate("() => document.body ? document.body.innerHTML : ''")
                finally:
                    # Always release the browser process, even if navigation fails
                    await browser.close()

                if content_html:
                    import markdownify  # type: ignore
                    return markdownify.markdownify(content_html, heading_style="ATX").strip()
                return ""
        except Exception as e:
            # If the browser executable is missing, attempt to install browsers automatically (once)
            import subprocess, sys
            if _allow_install and "Executable doesn't exist" in str(e):
                print("[PlaywrightIngestionPlugin] browsers missing, installing...", flush=True)
                try:
                    # Run the (slow) install off the event loop
                    await asyncio.to_thread(
                        subprocess.run,
                        [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                        check=True,
                    )
                    # Retry after installation, without attempting another install
                    return await self.scrape(url, _allow_install=False)
                except Exception as install_err:
                    print(f"[PlaywrightIngestionPlugin] failed to install browsers: {install_err}", flush=True)
            print(f"[PlaywrightIngestionPlugin] error scraping {url}: {e}", flush=True)
            return ""
