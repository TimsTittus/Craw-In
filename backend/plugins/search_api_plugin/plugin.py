import asyncio
import os
import time
from typing import Dict, Any, List, Optional
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, unquote

from backend.plugins.base import BasePlugin, PluginManifest

class SimpleTTLCache:
    """A simple in-memory cache with Time-To-Live (TTL) expiration."""
    def __init__(self, ttl_seconds: float = 300.0):
        self.ttl = ttl_seconds
        self.cache: Dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Optional[Any]:
        if key in self.cache:
            val, expiry = self.cache[key]
            if time.time() < expiry:
                return val
            # Clean up expired entry
            del self.cache[key]
        return None

    def set(self, key: str, value: Any) -> None:
        self.cache[key] = (value, time.time() + self.ttl)

    def clear(self) -> None:
        self.cache.clear()

class SearchApiPlugin(BasePlugin):
    """Web Search & SERP Router plugin utilizing Jina AI's search endpoint
    with a keyless HTML-only DuckDuckGo scraper fallback.

    Provides high performance through connection pooling, result caching,
    resilient retry handling, and automatic local search fallback.
    """
    def __init__(self, manifest: PluginManifest, config: Dict[str, Any] = None):
        super().__init__(manifest, config)
        self.base_url = "https://s.jina.ai/"
        
        # Initialize headers
        self.headers = {"Accept": "application/json"}
        
        # Load API key from config or environment variables
        plugin_config = config or {}
        api_key = plugin_config.get("jina_api_key") or os.getenv("JINA_API_KEY")
        self.has_api_key = bool(api_key)
        
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
            
        # Connection pooling: lazily initialized in execute
        self._client: Optional[httpx.AsyncClient] = None
        
        # TTL Caching: Default to 5 minutes (300 seconds)
        cache_ttl = float(plugin_config.get("cache_ttl", 300.0))
        self.cache = SimpleTTLCache(ttl_seconds=cache_ttl)

    def _get_client(self) -> httpx.AsyncClient:
        """Lazily initialize and return a shared httpx.AsyncClient for Jina."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=15.0,
                headers=self.headers,
                follow_redirects=True
            )
        return self._client

    async def _search_duckduckgo(self, search_target: str) -> List[Dict[str, Any]]:
        """Scrape DuckDuckGo's keyless HTML-only search endpoint."""
        print(f"[SearchApiPlugin] Activating keyless DuckDuckGo search fallback for: '{search_target}'", flush=True)
        url = "https://html.duckduckgo.com/html/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        
        # Use a fresh client without the JSON Accept/Auth headers required by Jina
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                response = await client.post(url, data={"q": search_target}, headers=headers)
                if response.status_code != 200:
                    return [{"error": f"DuckDuckGo search failed with status {response.status_code}"}]
                
                soup = BeautifulSoup(response.text, "html.parser")
                results = []
                for div in soup.find_all("div", class_="web-result"):
                    link_el = div.find("a", class_="result__a")
                    snippet_el = div.find("a", class_="result__snippet")
                    if link_el:
                        title = link_el.get_text(strip=True)
                        href = link_el.get("href", "")
                        
                        # Clean DuckDuckGo outbound proxy redirect if present
                        if "uddg=" in href:
                            try:
                                parsed = urlparse(href)
                                queries = parse_qs(parsed.query)
                                if "uddg" in queries:
                                    href = unquote(queries["uddg"][0])
                            except Exception:
                                pass
                                
                        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                        results.append({
                            "title": title,
                            "url": href,
                            "content": snippet
                        })
                
                print(f"[SearchApiPlugin] DuckDuckGo fallback fetched {len(results)} results.", flush=True)
                return results
            except Exception as e:
                return [{"error": f"DuckDuckGo search scraper encountered an error: {e}"}]

    async def execute(self, query: str, context: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Execute the search query.

        Args:
            query: The search term or phrase.
            context: Optional runtime context dictionary, which may contain
                     a nested 'filters' dictionary or filters directly.
        """
        if not query:
            return [{"error": "Empty search query provided."}]

        # Robust context/filter parsing
        ctx = context or {}
        filters = ctx.get("filters", ctx) if isinstance(ctx.get("filters"), dict) else ctx
        
        site_filter = filters.get("site")
        search_target = query
        if site_filter:
            search_target = f"{query} site:{site_filter}"

        # 1. Check Cache
        cache_key = f"{search_target}"
        cached_result = self.cache.get(cache_key)
        if cached_result is not None:
            return cached_result

        results = []
        
        # 2. Use DuckDuckGo directly if no key is provided
        if not self.has_api_key:
            results = await self._search_duckduckgo(search_target)
        else:
            # Try Jina AI Search
            client = self._get_client()
            url = f"{self.base_url}{search_target}"
            max_retries = 3
            initial_delay = 0.5
            delay = initial_delay
            response = None
            last_error = None
            
            for attempt in range(max_retries):
                try:
                    response = await client.get(url)
                    # Success
                    if response.status_code == 200:
                        break
                    # Retryable status codes (e.g. Rate Limit 429, Server Error 5xx)
                    if response.status_code == 429 or 500 <= response.status_code < 600:
                        last_error = f"API returned status {response.status_code}"
                    else:
                        # Non-retryable client error (e.g., 400, 401, 403, 404)
                        last_error = f"API returned non-retryable status {response.status_code}"
                        try:
                            err_json = response.json()
                            if "Authentication" in err_json.get("message", "") or response.status_code == 401:
                                last_error = "Authentication required. Please configure a valid JINA_API_KEY."
                        except Exception:
                            pass
                        break
                except httpx.RequestError as exc:
                    last_error = f"Network request error: {exc}"
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    last_error = f"{last_error} (Max retries reached)"

            # Parse results if request was successful
            if response is not None and response.status_code == 200:
                try:
                    data = response.json()
                    results = data.get("data", [])
                    if results is None:
                        results = []
                except Exception as e:
                    last_error = f"Failed to parse Jina search response: {e}"

            # 3. Handle network/server failure with DuckDuckGo fallback
            is_auth_error = response is not None and (response.status_code == 401 or response.status_code == 403)
            
            # If search failed, and it is NOT an authentication failure, trigger the keyless fallback
            if (not results or (len(results) == 1 and "error" in results[0])) and not is_auth_error:
                print(f"[SearchApiPlugin] Jina AI search failed ({last_error}). Triggering DuckDuckGo fallback...", flush=True)
                results = await self._search_duckduckgo(search_target)
            elif is_auth_error:
                # If it was a hard authentication error, return it immediately so the user is aware
                return [{"error": last_error}]

        # 4. Cache and return results
        if results and "error" not in results[0]:
            self.cache.set(cache_key, results)
            
        return results

    async def close(self) -> None:
        """Close the HTTP client when disposing the plugin."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
