"""
person_recon_plugin/plugin.py
─────────────────────────────────────────────────────────────────────────────
Person Intelligence Reconnaissance Plugin for Craw-In.

Pipeline:
  1. Targeted SERP Matrix Generation — platform-specific + broad fallback queries
  2. Concurrent Async Ingestion       — staggered batches with per-query retry
  3. Direct Username URL Probing     — checks direct handles across major networks
  4. ULTRA-STRICT NAME GROUNDING      — requires target person's name in URL/Title/Snippet;
                                        eliminates all random company posts & unrelated people.
  5. MULTI-ANCHOR CONTEXT BOOSTING   — boosts score for location, company, role, college matches.
  6. DEEP PAGE CONTENT EXTRACTION    — concurrently scrapes full page text for top
                                        verified person matches using ConcurrentIngestionEngine
  7. KNOWLEDGE STORE INDEXING         — auto-indexes full page text into Qdrant & SQLite
  8. Entity Consolidation & Dossier   — compiles pristine 360° intelligence dossier
"""

import asyncio
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx

from backend.plugins.base import BasePlugin, PluginManifest

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_SCORE_CAP: float = 0.99
_SNIPPET_MAX_CHARS: int = 1_000
_FULL_TEXT_MAX_CHARS: int = 8_000
_MAX_RESULTS_PER_PLATFORM: int = 10
_TOP_N: int = 15
_DEEP_SCRAPE_TOP_K: int = 6

# HTTP
_JINA_TIMEOUT: float = 25.0
_DDG_TIMEOUT: float = 14.0
_RETRY_ATTEMPTS: int = 2
_RETRY_BASE_DELAY: float = 1.0
_BATCH_STAGGER_DELAY: float = 0.4

# Endpoints
_JINA_SERP_BASE = "https://s.jina.ai/"
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"

_DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ─────────────────────────────────────────────────────────────────────────────
# Platform query templates
# ─────────────────────────────────────────────────────────────────────────────

_PLATFORM_TEMPLATES = [
    {
        "platform": "linkedin",
        "jina_query":  'site:linkedin.com/in/ "{name}" {anchor_str}',
        "ddg_query":   '"{name}" {anchor_str} linkedin profile',
        "broad_query": '"{name}" {anchor_str} linkedin',
    },
    {
        "platform": "github",
        "jina_query":  'site:github.com "{name}" {anchor_str}',
        "ddg_query":   '"{name}" {anchor_str} github',
        "broad_query": '"{name}" github developer',
    },
    {
        "platform": "twitter",
        "jina_query":  'site:x.com "{name}" {anchor_str}',
        "ddg_query":   '"{name}" {anchor_str} twitter OR x.com',
        "broad_query": '"{name}" twitter',
    },
    {
        "platform": "news",
        "jina_query":  '"{name}" {anchor_str} (interview OR article OR awarded OR announces)',
        "ddg_query":   '"{name}" {anchor_str} interview article',
        "broad_query": '"{name}" news interview',
    },
    {
        "platform": "scholar",
        "jina_query":  'site:scholar.google.com "{name}" {anchor_str}',
        "ddg_query":   '"{name}" {anchor_str} research paper scholar',
        "broad_query": '"{name}" research publications',
    },
    {
        "platform": "corporate",
        "jina_query":  '(site:crunchbase.com OR site:bloomberg.com) "{name}" {anchor_str}',
        "ddg_query":   '"{name}" {anchor_str} crunchbase bloomberg profile',
        "broad_query": '"{name}" corporate executive crunchbase',
    },
    {
        "platform": "social_blogs",
        "jina_query":  '(site:medium.com OR site:substack.com OR site:youtube.com) "{name}" {anchor_str}',
        "ddg_query":   '"{name}" {anchor_str} medium substack youtube',
        "broad_query": '"{name}" posts articles video',
    },
    {
        "platform": "general_web",
        "jina_query":  '"{name}" {anchor_str} (bio OR portfolio OR about OR resume OR wikipedia)',
        "ddg_query":   '"{name}" {anchor_str} bio portfolio about wikipedia',
        "broad_query": '"{name}" bio about wikipedia',
    },
]

_DIRECT_PROFILE_URLS = {
    "linkedin":  "https://www.linkedin.com/in/{username}",
    "github":    "https://github.com/{username}",
    "twitter":   "https://x.com/{username}",
    "medium":    "https://medium.com/@{username}",
    "substack":  "https://{username}.substack.com",
}

# ─────────────────────────────────────────────────────────────────────────────
# Plugin
# ─────────────────────────────────────────────────────────────────────────────

class PersonReconPlugin(BasePlugin):
    """Multi-platform OSINT reconnaissance with ultra-strict name-grounded
    disambiguation and full page content ingestion.
    """

    def __init__(self, manifest: PluginManifest, config: Dict[str, Any] = None) -> None:
        super().__init__(manifest, config)

        cfg = config or {}
        api_key: Optional[str] = cfg.get("jina_api_key") or os.getenv("JINA_API_KEY")
        self._has_api_key: bool = bool(api_key)

        self._jina_headers: Dict[str, str] = {"Accept": "application/json"}
        if api_key:
            self._jina_headers["Authorization"] = f"Bearer {api_key}"

        self._client: Optional[httpx.AsyncClient] = None

    # ── HTTP Client ───────────────────────────────────────────────────────────

    def _get_client(self) -> httpx.AsyncClient:
        """Lazy-init a shared async client for Jina requests."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=_JINA_TIMEOUT,
                headers=self._jina_headers,
                follow_redirects=True,
            )
        return self._client

    # ── Query building ────────────────────────────────────────────────────────

    @staticmethod
    def _build_anchor_string(context: Dict[str, Any]) -> str:
        """Serialize context anchors into a search fragment."""
        parts: List[str] = []
        for field in ("company", "location", "role", "college"):
            val = context.get(field)
            if val and isinstance(val, str) and val.strip():
                parts.append(val.strip())
        username = context.get("username")
        if username and isinstance(username, str) and username.strip():
            parts.append(username.strip())
        return " ".join(parts)

    @staticmethod
    def _expand_query(template: str, name: str, anchor_str: str) -> str:
        """Fill template placeholders and normalise whitespace."""
        q = template.format(name=name, anchor_str=anchor_str).strip()
        return re.sub(r"\s{2,}", " ", q)

    # ── Jina SERP fetch ───────────────────────────────────────────────────────

    async def _fetch_jina(
        self, client: httpx.AsyncClient, query: str
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Fetch results from Jina SERP. Returns (results, error_or_None)."""
        encoded_query = quote(query, safe="")
        url = f"{_JINA_SERP_BASE}{encoded_query}"

        last_error: Optional[str] = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                res = await client.get(url)
                if res.status_code == 200:
                    data = res.json().get("data", []) or []
                    return data, None
                if res.status_code in (429, 500, 502, 503, 504):
                    last_error = f"Jina HTTP {res.status_code}"
                    await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** attempt))
                    continue
                return [], f"Jina HTTP {res.status_code}"
            except httpx.TimeoutException:
                last_error = "Jina timeout"
                await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** attempt))
            except Exception as exc:
                return [], f"Jina error: {exc}"

        return [], last_error

    # ── DuckDuckGo fetch ──────────────────────────────────────────────────────

    async def _fetch_ddg(self, query: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Fetch results from DuckDuckGo HTML endpoint."""
        last_error: Optional[str] = None

        for attempt in range(_RETRY_ATTEMPTS):
            try:
                async with httpx.AsyncClient(timeout=_DDG_TIMEOUT) as ddg_client:
                    res = await ddg_client.post(
                        _DDG_HTML_URL, data={"q": query}, headers=_DDG_HEADERS
                    )
                    if res.status_code != 200:
                        last_error = f"DDG HTTP {res.status_code}"
                        if res.status_code in (202, 429, 500, 502, 503):
                            await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** attempt))
                            continue
                        return [], last_error

                    try:
                        from bs4 import BeautifulSoup  # type: ignore
                    except ImportError:
                        return [], "BeautifulSoup not installed"

                    soup = BeautifulSoup(res.text, "html.parser")
                    results: List[Dict[str, Any]] = []
                    for div in soup.find_all("div", class_="web-result"):
                        link_el = div.find("a", class_="result__a")
                        snippet_el = div.find("a", class_="result__snippet")
                        if not link_el:
                            continue
                        title = link_el.get_text(strip=True)
                        href: str = link_el.get("href", "")
                        if "uddg=" in href:
                            try:
                                qs = parse_qs(urlparse(href).query)
                                if "uddg" in qs:
                                    href = unquote(qs["uddg"][0])
                            except Exception:
                                pass
                        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                        results.append({"title": title, "url": href, "content": snippet})

                    if results:
                        return results, None
                    last_error = "DDG returned 0 results"
                    await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** attempt))

            except httpx.TimeoutException:
                last_error = "DDG timeout"
                await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** attempt))
            except Exception as exc:
                return [], f"DDG error: {exc}"

        return [], last_error

    # ── Direct username URL probing ───────────────────────────────────────────

    async def _probe_username_urls(
        self, username: str, name: str
    ) -> List[Dict[str, Any]]:
        """Probe direct profile URLs for a known username."""
        results: List[Dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            for platform, url_template in _DIRECT_PROFILE_URLS.items():
                url = url_template.format(username=username)
                try:
                    res = await client.head(url)
                    if res.status_code == 200:
                        results.append({
                            "title": f"{name} — {platform.title()} Profile",
                            "url": str(res.url),
                            "content": f"Direct profile URL verified for @{username}",
                            "_platform": platform,
                        })
                except Exception:
                    pass
        return results

    # ── Per-platform orchestrator ─────────────────────────────────────────────

    async def _fetch_platform(
        self,
        client: Optional[httpx.AsyncClient],
        platform_def: Dict[str, str],
        name: str,
        anchor_str: str,
    ) -> Dict[str, Any]:
        """Fetch results for one platform with multi-tier fallback."""
        platform = platform_def["platform"]
        all_results: List[Dict[str, Any]] = []
        errors: List[str] = []

        if self._has_api_key and client is not None:
            narrow_q = self._expand_query(platform_def["jina_query"], name, anchor_str)
            results, err = await self._fetch_jina(client, narrow_q)
            if results:
                all_results.extend(results)
            if err:
                errors.append(f"jina_narrow: {err}")

        if not all_results and self._has_api_key and client is not None:
            broad_q = self._expand_query(platform_def["broad_query"], name, anchor_str)
            results, err = await self._fetch_jina(client, broad_q)
            if results:
                all_results.extend(results)
            if err:
                errors.append(f"jina_broad: {err}")

        if not all_results:
            ddg_q = self._expand_query(platform_def["ddg_query"], name, anchor_str)
            results, err = await self._fetch_ddg(ddg_q)
            if results:
                all_results.extend(results)
            if err:
                errors.append(f"ddg: {err}")

        log_status = f"{len(all_results)} results" if all_results else f"FAILED [{'; '.join(errors)}]"
        print(f"[PersonReconPlugin] {platform:12s} → {log_status}", flush=True)

        return {"platform": platform, "results": all_results, "errors": errors}

    # ── Deep page scraping & content extraction ────────────────────────────────

    async def _deep_scrape_pages(self, urls: List[str]) -> Dict[str, str]:
        """Scrape full Markdown page content for top target URLs concurrently."""
        try:
            from backend.crawler import ConcurrentIngestionEngine
            crawler = ConcurrentIngestionEngine(concurrency_limit=5)
            
            scraped_data: Dict[str, str] = {}
            tasks = [crawler.process_url(u) for u in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for url, res in zip(urls, results):
                if isinstance(res, dict) and res.get("status") == 200:
                    content = res.get("content", "") or ""
                    if content and len(content) > 100:
                        scraped_data[url] = content
            return scraped_data
        except Exception as exc:
            print(f"[PersonReconPlugin] Deep scrape error: {exc}", flush=True)
            return {}

    # ── Ultra-Strict Name Grounding & Disambiguation Scoring ─────────────────

    def _calculate_confidence(
        self, target_name: str, url: str, title: str, snippet: str, context: Dict[str, Any]
    ) -> float:
        """Calculate grounded match confidence for target person.
        
        Requires MANDATORY presence of target person's name in URL, Title, or direct Snippet.
        Returns 0.0 for unrelated pages (e.g. company-only posts or unrelated individuals).
        """
        url_lower = url.lower()
        title_lower = title.lower()
        snippet_lower = snippet.lower()
        text_combined = f"{title_lower} {snippet_lower} {url_lower}"

        # Reject generic company pages (e.g. linkedin.com/company/) unless title explicitly mentions person
        if "linkedin.com/company/" in url_lower and not any(p in title_lower for p in target_name.lower().split()):
            return 0.0

        name_parts = [p.lower() for p in target_name.split() if len(p) > 1]
        if not name_parts:
            return 0.0

        first_name = name_parts[0]
        last_name = name_parts[-1] if len(name_parts) > 1 else ""

        # 1. Mandatory Name Grounding: URL or Title must contain first_name (and last_name if present),
        # OR snippet must explicitly contain full_name_clean.
        url_has_name = (first_name in url_lower) and (last_name in url_lower if last_name else True)
        title_has_name = (first_name in title_lower) and (last_name in title_lower if last_name else True)

        if not (url_has_name or title_has_name):
            full_name_clean = " ".join(name_parts)
            if full_name_clean not in snippet_lower:
                return 0.0

        # 2. Base score calculation
        score = 0.70 if url_has_name else 0.50

        # 3. Multi-Anchor Context Boost (Company / Location / Role / College)
        for anchor_key in ("company", "location", "role", "college"):
            val = context.get(anchor_key, "")
            if val and isinstance(val, str) and val.strip():
                if val.strip().lower() in text_combined:
                    score += 0.25

        return round(min(score, _SCORE_CAP), 2)

    @staticmethod
    def _clean_profile_title(url: str, title: str, target_name: str) -> str:
        """Clean up useless Authwall titles like 'Sign Up | LinkedIn' into human-readable profile titles."""
        t_lower = title.lower()
        if "sign up" in t_lower or "authwall" in url.lower() or "join linkedin" in t_lower:
            if "linkedin.com" in url.lower():
                return f"{target_name} — LinkedIn Profile"
        return title

    # ── Platform inferral from URL ────────────────────────────────────────────

    @staticmethod
    def _infer_platform(url: str, declared_platform: str) -> str:
        """Refine platform label from the actual URL domain."""
        lower = url.lower()
        if "linkedin.com" in lower:
            return "linkedin"
        if "github.com" in lower:
            return "github"
        if "twitter.com" in lower or "x.com" in lower:
            return "twitter"
        if "wikipedia.org" in lower:
            return "wikipedia"
        if "crunchbase.com" in lower:
            return "crunchbase"
        if "bloomberg.com" in lower:
            return "bloomberg"
        if any(d in lower for d in ("scholar.google", "researchgate.net", "academia.edu", "arxiv.org")):
            return "scholar"
        if any(d in lower for d in ("medium.com", "substack.com", "dev.to", "hashnode")):
            return "blog"
        if any(d in lower for d in ("youtube.com", "youtu.be")):
            return "youtube"
        return declared_platform

    # ── Public execute ────────────────────────────────────────────────────────

    async def execute(self, query: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Run the complete person reconnaissance and deep ingestion pipeline.

        Args:
            query   : Target person's full name.
            context : Anchor dict — company, location, role, college, username, skills.

        Returns:
            A single-element list with the structured intelligence dossier.
        """
        person_name = (query or "").strip()
        if not person_name:
            return [{"error": "Target person name is required."}]

        ctx = context or {}
        anchor_str = self._build_anchor_string(ctx)
        client = self._get_client() if self._has_api_key else None

        print(
            f"[PersonReconPlugin] Starting 360° Recon for '{person_name}' "
            f"across {len(_PLATFORM_TEMPLATES)} platforms | "
            f"anchors: {anchor_str or '(none)'}",
            flush=True,
        )

        # ── Phase 1: Staggered parallel SERP matrix ──
        batch_1 = _PLATFORM_TEMPLATES[:4]
        batch_2 = _PLATFORM_TEMPLATES[4:]

        tasks_1 = [self._fetch_platform(client, pdef, person_name, anchor_str) for pdef in batch_1]
        responses_1 = await asyncio.gather(*tasks_1)

        await asyncio.sleep(_BATCH_STAGGER_DELAY)

        tasks_2 = [self._fetch_platform(client, pdef, person_name, anchor_str) for pdef in batch_2]
        responses_2 = await asyncio.gather(*tasks_2)

        serp_responses: List[Dict[str, Any]] = list(responses_1) + list(responses_2)

        # ── Phase 1b: Direct username probing ──
        username = ctx.get("username")
        username_results: List[Dict[str, Any]] = []
        if username and isinstance(username, str) and username.strip():
            clean_username = username.strip().lstrip("@")
            username_results = await self._probe_username_urls(clean_username, person_name)

        # ── Phase 2: Aggregate, deduplicate, score & ground ──
        seen_urls: set = set()
        profiles: List[Dict[str, Any]] = []

        for item in username_results:
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            profiles.append({
                "target_name": person_name,
                "platform": item.get("_platform", "general_web"),
                "title": self._clean_profile_title(url, item.get("title", ""), person_name),
                "url": url,
                "match_confidence": _SCORE_CAP,
                "snippet": item.get("content", "")[:_SNIPPET_MAX_CHARS],
                "full_text": "",
            })

        for response in serp_responses:
            declared_platform = response["platform"]
            for item in response.get("results", [])[:_MAX_RESULTS_PER_PLATFORM]:
                url: Optional[str] = item.get("url")
                if not url or url in seen_urls:
                    continue

                snippet = item.get("content", "") or item.get("description", "") or ""
                title = item.get("title", "")
                
                # Strict name-grounding verification
                confidence = self._calculate_confidence(person_name, url, title, snippet, ctx)
                if confidence <= 0.0:
                    # Discard false positives completely (company posts or unrelated individuals)
                    continue

                seen_urls.add(url)
                clean_title = self._clean_profile_title(url, title, person_name)

                profiles.append({
                    "target_name": person_name,
                    "platform": self._infer_platform(url, declared_platform),
                    "title": clean_title,
                    "url": url,
                    "match_confidence": confidence,
                    "snippet": snippet[:_SNIPPET_MAX_CHARS],
                    "full_text": snippet,
                })

        # Sort descending by match confidence
        profiles.sort(key=lambda x: x["match_confidence"], reverse=True)

        # ── Phase 3: DEEP PAGE SCRAPING & CONTENT EXTRACTION ──
        top_targets = profiles[:_DEEP_SCRAPE_TOP_K]
        top_urls = [p["url"] for p in top_targets if p.get("url")]

        if top_urls:
            print(f"[PersonReconPlugin] Deep-scraping full page content for top {len(top_urls)} verified matches...", flush=True)
            scraped_content = await self._deep_scrape_pages(top_urls)
            
            for profile in profiles:
                url = profile.get("url")
                if url in scraped_content:
                    full_md = scraped_content[url]
                    profile["full_text"] = full_md[:_FULL_TEXT_MAX_CHARS]
                    profile["content_length_bytes"] = len(full_md)

            # ── Phase 4: AUTO-INDEX INTO VECTOR & KEYWORD MEMORY ──
            try:
                from backend.ingestion import ingest_urls
                print(f"[PersonReconPlugin] Auto-indexing top {len(top_urls)} URLs into Qdrant & SQLite...", flush=True)
                asyncio.create_task(ingest_urls(top_urls))
            except Exception as exc:
                print(f"[PersonReconPlugin] Background indexing notice: {exc}", flush=True)

        # Collect platform status diagnostics
        platform_status: Dict[str, str] = {}
        for resp in serp_responses:
            p = resp["platform"]
            count = len(resp.get("results", []))
            errs = resp.get("errors", [])
            if count > 0:
                platform_status[p] = f"{count} results"
            elif errs:
                platform_status[p] = f"failed: {'; '.join(errs)}"
            else:
                platform_status[p] = "0 results"

        anchors_used = {k: v for k, v in ctx.items() if v}

        print(
            f"[PersonReconPlugin] 360° Recon complete for '{person_name}' — "
            f"{len(profiles)} verified profiles & footprints | "
            f"deep-scraped: {len(top_urls)} full pages.",
            flush=True,
        )

        return [
            {
                "query_target": person_name,
                "anchors_used": anchors_used,
                "total_matches_found": len(profiles),
                "platform_status": platform_status,
                "deep_scraped_count": len(top_urls),
                "top_matches": profiles[:_TOP_N],
            }
        ]

    async def close(self) -> None:
        """Release the shared HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()