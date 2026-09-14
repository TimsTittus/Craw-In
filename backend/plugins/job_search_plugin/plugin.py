"""
job_search_plugin/plugin.py
─────────────────────────────────────────────────────────────────────────────
Job Search Plugin for Craw-In.

Two-stage pipeline — many cheap searches, few expensive crawls:
  1. Query understanding  — rule-based parser, optionally refined by the user's LLM (grounded)
  2. Query expansion      — bounded role × location × source search matrix
  3. Search discovery     — concurrent queries through the shared search_api plugin
                            (Jina SERP with DuckDuckGo fallback and TTL cache)
  4. Candidate pool       — URL canonicalisation, level-1 dedup, snippet pre-ranking
  5. Deep crawl           — top candidates only, via ConcurrentIngestionEngine
                            (trafilatura → markdownify → Playwright), SSRF-guarded;
                            listing pages contribute their posting links to a 2nd wave
  6. Extraction           — schema.org JobPosting → heuristics → grounded LLM fallback
  7. Deduplication        — platform id / URL → fingerprint → description similarity
  8. Ranking              — explainable weighted score with reasons and concerns
  9. Verification         — HTTP check of the chosen application URLs

Context keys accepted by ``execute``:
  search     : object with ``async execute(query, context)`` (the search_api plugin)
  llm_config : optional LLMConfig dict — enables LLM assistance
  filters    : optional structured filters (see ``JobSearchFilters``)
  emit       : optional ``async (event, step, status, details)`` progress callback
"""

import asyncio
import datetime
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Tuple

import httpx

from backend.crawler import ConcurrentIngestionEngine
from backend.plugins.base import BasePlugin, PluginManifest
from backend.plugins.job_search_plugin.extract import page_signals_closed, harvest_posting_links, parse_job_page, visible_text
from backend.plugins.job_search_plugin.llm_assist import llm_extract_job_fields
from backend.plugins.job_search_plugin.match import (
    Candidate,
    looks_like_job_candidate,
    merge_duplicates,
    prerank,
    rank_jobs,
    select_for_crawl,
)
from backend.plugins.job_search_plugin.normalize import utcnow
from backend.plugins.job_search_plugin.query import build_query, generate_search_queries
from backend.plugins.job_search_plugin.schema import (
    JobPosting,
    JobSearchQuery,
    JobSearchReport,
    JobSearchSummary,
    JobStatus,
    SearchQueryStatus,
)
from backend.plugins.job_search_plugin.urls import (
    BlockedURLError,
    canonicalize_url,
    classify_url,
    dedup_key,
    guard_request,
    hostname,
    is_generic_landing,
    is_non_job_domain,
    is_public_url,
    unwrap_redirect,
)
from backend.plugins.search_api_plugin.plugin import SimpleTTLCache

# ─────────────────────────────────────────────────────────────────────────────
# Limits — every stage is bounded
# ─────────────────────────────────────────────────────────────────────────────

_SEARCH_CONCURRENCY: int = 4
_SEARCH_TIMEOUT_S: float = 60.0      # search_api retries Jina 3× (15s each) before its DuckDuckGo fallback
_RESULTS_PER_QUERY: int = 10
_MAX_CANDIDATES: int = 150

_CRAWL_BUDGET: int = 36            # total pages deep-crawled per search (both waves)
_FIRST_WAVE_MAX: int = 28          # leaves room for postings harvested from listing pages
_CRAWL_PER_HOST: int = 8           # per-host share of the first wave
_CRAWL_CONCURRENCY: int = 6
_HOST_CONCURRENCY: int = 2         # simultaneous requests to one host
_CRAWL_TIMEOUT_S: float = 45.0     # covers the Playwright fallback
_HARVEST_PER_PAGE: int = 5

_LLM_EXTRACT_MAX_PAGES: int = 4
_LLM_EXTRACT_CONCURRENCY: int = 2
_LLM_MIN_TEXT_CHARS: int = 300

_VERIFY_EXTRA: int = 5             # verify a few beyond the limit to backfill closed postings
_VERIFY_CONCURRENCY: int = 6
_VERIFY_TIMEOUT_S: float = 10.0
_VERIFY_ATTEMPTS: int = 2
_VERIFY_BACKOFF_S: float = 1.0
_VERIFY_MAX_REDIRECTS: int = 5
_VERIFY_MAX_BODY_BYTES: int = 400_000

_PAGE_CACHE_TTL_S: float = 1_800.0   # extracted pages
_VERIFY_CACHE_TTL_S: float = 600.0   # availability changes — keep this short

EmitFn = Callable[[str, str, str, str], Awaitable[None]]
PageMap = Dict[str, Dict[str, Any]]


class SearchProvider(Protocol):
    async def execute(self, query: str, context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]: ...


async def _silent_emit(event: str, step: str, status: str, details: str) -> None:
    return None


def _describe_spec(spec: JobSearchQuery, parser: str) -> str:
    parts = []
    if spec.roles or spec.keywords:
        parts.append("Roles: " + ", ".join(spec.roles or spec.keywords))
    if spec.skills:
        parts.append("Skills: " + ", ".join(spec.skills))
    if spec.locations or spec.remote != "any":
        remote = {"remote_ok": " (remote OK)", "remote_only": " (remote only)", "onsite_only": " (on-site only)"}
        parts.append("Locations: " + (", ".join(spec.locations) or "anywhere") + remote.get(spec.remote, ""))
    if spec.employment_types:
        parts.append("Type: " + ", ".join(t.replace("_", "-") for t in spec.employment_types))
    if spec.min_experience_years is not None or spec.max_experience_years is not None:
        low = f"{spec.min_experience_years:g}" if spec.min_experience_years is not None else "0"
        high = f"{spec.max_experience_years:g}" if spec.max_experience_years is not None else "+"
        parts.append(f"Experience: {low}–{high} yrs")
    elif spec.experience_level:
        parts.append(f"Level: {spec.experience_level}")
    if spec.posted_within_days:
        parts.append(f"Posted within {spec.posted_within_days} days")
    return " · ".join(parts) + f" — via {parser}"


class JobSearchPlugin(BasePlugin):
    """Finds, deduplicates, ranks and verifies job postings for natural-language requirements."""

    def __init__(self, manifest: PluginManifest, config: Dict[str, Any] = None) -> None:
        super().__init__(manifest, config)
        # Reuse the shared multi-tier crawler. The request hook re-validates every httpx redirect hop;
        # the URL guard does the same for every request made by the Playwright fallback browser.
        self._crawler = ConcurrentIngestionEngine(
            concurrency_limit=_CRAWL_CONCURRENCY, event_hooks={"request": [guard_request]}, url_guard=is_public_url
        )
        self._page_cache = SimpleTTLCache(ttl_seconds=_PAGE_CACHE_TTL_S)
        self._verify_cache = SimpleTTLCache(ttl_seconds=_VERIFY_CACHE_TTL_S)

    # ── Public execute ────────────────────────────────────────────────────────

    async def execute(self, query: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Run the full job search. Returns a single-element list holding a ``JobSearchReport`` dict."""
        ctx = context or {}
        emit: EmitFn = ctx.get("emit") or _silent_emit
        search: Optional[SearchProvider] = ctx.get("search")
        llm_config: Optional[Dict[str, Any]] = ctx.get("llm_config") or None
        started = time.monotonic()
        now = utcnow()
        summary = JobSearchSummary()

        await emit("job_search_started", "Job Search", "running", "Job search started.")
        try:
            spec, parser = await build_query(query, ctx.get("filters"), llm_config)
        except ValueError as exc:
            return await self._fail(emit, {"original": query}, summary, str(exc), started)
        query_info = {"original": spec.original, "normalized": spec.model_dump(mode="json"), "parser": parser}
        await emit("query_parsed", "Analyzing Requirements", "completed", _describe_spec(spec, parser))

        if search is None:
            return await self._fail(emit, query_info, summary, "Search service is unavailable.", started)

        queries = generate_search_queries(spec)
        summary.queries_generated = len(queries)
        preview = " · ".join(queries[:3]) + (" …" if len(queries) > 3 else "")
        await emit("queries_generated", "Query Expansion", "completed", f"Generated {len(queries)} search queries: {preview}")

        results, statuses = await self._search_all(search, queries, emit)
        summary.searches_failed = sum(s.status == "failed" for s in statuses)
        if summary.searches_failed == len(queries):
            return await self._fail(emit, query_info, summary,
                                    "All search queries failed. Check network access or the search provider.",
                                    started, statuses)
        warnings: List[str] = []
        if summary.searches_failed:
            warnings.append(f"{summary.searches_failed} of {len(queries)} search queries failed; results may be incomplete.")

        candidates = self._build_candidates(results)
        summary.discovered, summary.unique = len(results), len(candidates)
        await emit("candidates_discovered", "Candidate Pool", "completed", f"{len(results)} candidate results discovered.")
        await emit("deduplication_completed", "Deduplication", "completed",
                   f"{len(candidates)} unique job candidates after URL normalisation "
                   f"({len(results) - len(candidates)} duplicates or non-job links dropped).")

        await emit("ranking_started", "Candidate Pre-Ranking", "running", "Ranking candidates by title, skills and location...")
        first_wave = select_for_crawl(prerank(candidates, spec), _FIRST_WAVE_MAX, _CRAWL_PER_HOST)
        await emit("ranking_started", "Candidate Pre-Ranking", "completed",
                   f"Selected top {len(first_wave)} of {len(candidates)} candidates for deep crawling.")

        jobs, pages = await self._crawl_and_extract(first_wave, llm_config, now, summary, emit)
        if summary.crawl_failures:
            warnings.append(f"{summary.crawl_failures} pages could not be fetched (blocked, rate-limited or unavailable).")

        jobs, summary.duplicates_removed = merge_duplicates(jobs)
        summary.application_links_found = sum(j.application_type in ("direct_company", "ats") for j in jobs)
        await emit("application_link_found", "Application Links", "completed",
                   f"{len(jobs)} unique jobs after merging {summary.duplicates_removed} cross-source duplicates; "
                   f"direct company/ATS application links for {summary.application_links_found}.")

        qualified, rejected = rank_jobs(jobs, spec, now)
        summary.qualified = len(qualified)
        shortlist = qualified[: spec.limit + _VERIFY_EXTRA]

        await emit("verification_completed", "Verifying Applications", "running",
                   f"Checking {len(shortlist)} application links...")
        await self._verify_jobs(shortlist, pages)
        verified, dropped = rank_jobs(shortlist, spec, now)  # re-score with verified statuses; drops closed postings
        final = verified[: spec.limit]
        summary.rejected = rejected + dropped
        summary.returned = len(final)
        summary.verified_applications = sum(j.application_verified and j.status == "active" for j in final)
        await emit("verification_completed", "Verifying Applications", "completed",
                   f"{summary.verified_applications} of {len(final)} application links verified active.")

        summary.duration_seconds = round(time.monotonic() - started, 2)
        await emit("job_search_completed", "Job Search", "completed",
                   f"Returned {len(final)} ranked jobs in {summary.duration_seconds:.1f}s.")
        print(f"[JobSearchPlugin] Completed: {summary.model_dump()}", flush=True)
        report = JobSearchReport(query=query_info, summary=summary, search_status=statuses, jobs=final, warnings=warnings)
        return [report.model_dump(mode="json")]

    async def _fail(
        self,
        emit: EmitFn,
        query_info: Dict[str, Any],
        summary: JobSearchSummary,
        message: str,
        started: float,
        statuses: Optional[List[SearchQueryStatus]] = None,
    ) -> List[Dict[str, Any]]:
        summary.duration_seconds = round(time.monotonic() - started, 2)
        await emit("job_search_failed", "Job Search", "failed", message)
        print(f"[JobSearchPlugin] Failed: {message}", flush=True)
        report = JobSearchReport(query=query_info, summary=summary, search_status=statuses or [], error=message)
        return [report.model_dump(mode="json")]

    # ── Search discovery ──────────────────────────────────────────────────────

    async def _search_all(
        self, search: SearchProvider, queries: List[str], emit: EmitFn
    ) -> Tuple[List[Dict[str, Any]], List[SearchQueryStatus]]:
        """Run all queries with bounded concurrency; a failing query never fails the search."""
        semaphore = asyncio.Semaphore(_SEARCH_CONCURRENCY)
        progress = {"done": 0, "failed": 0}
        await emit("search_started", "Search Discovery", "running",
                   f"Searching {len(queries)} queries ({_SEARCH_CONCURRENCY} at a time)...")

        async def run(query: str) -> Tuple[List[Dict[str, Any]], SearchQueryStatus]:
            error: Optional[str] = None
            async with semaphore:
                try:
                    raw = await asyncio.wait_for(search.execute(query, {}), timeout=_SEARCH_TIMEOUT_S)
                except Exception as exc:
                    raw, error = [], f"{type(exc).__name__}"
            if error is None and raw and isinstance(raw[0], dict) and raw[0].get("error"):
                raw, error = [], str(raw[0]["error"])[:200]
            items = [r for r in (raw or []) if isinstance(r, dict) and r.get("url")][:_RESULTS_PER_QUERY]
            progress["done"] += 1
            progress["failed"] += error is not None
            await emit("search_source_completed", "Search Discovery", "running",
                       f"{progress['done']}/{len(queries)} queries completed ({progress['failed']} failed).")
            status = SearchQueryStatus(query=query, status="failed" if error else "ok", results=len(items), error=error)
            return items, status

        outcomes = await asyncio.gather(*(run(q) for q in queries))
        results = [item for items, _ in outcomes for item in items]
        statuses = [status for _, status in outcomes]
        failed = progress["failed"]
        await emit("search_source_completed", "Search Discovery", "failed" if failed == len(queries) else "completed",
                   f"{len(results)} results from {len(queries) - failed}/{len(queries)} queries"
                   + (f" ({failed} failed)." if failed else "."))
        return results, statuses

    @staticmethod
    def _build_candidates(results: List[Dict[str, Any]]) -> List[Candidate]:
        """Canonicalise result URLs, drop non-job links and merge repeats (level-1 dedup)."""
        pool: Dict[str, Candidate] = {}
        for item in results:
            url = canonicalize_url(unwrap_redirect(str(item.get("url", ""))))
            if not url or is_non_job_domain(url):
                continue
            title = str(item.get("title") or "")[:300]
            snippet = str(item.get("description") or item.get("content") or "")[:600]
            if not looks_like_job_candidate(url, title):
                continue
            key = dedup_key(url)
            if key in pool:
                pool[key].hits += 1
                pool[key].snippet = pool[key].snippet or snippet
                continue
            if len(pool) >= _MAX_CANDIDATES:
                continue
            pool[key] = Candidate(url=url, key=key, title=title, snippet=snippet, kind=classify_url(url))
        return list(pool.values())

    # ── Deep crawl & extraction ───────────────────────────────────────────────

    async def _fetch_page(self, url: str, host_slots: Dict[str, asyncio.Semaphore]) -> Dict[str, Any]:
        """Fetch one page through the shared crawler, with SSRF validation and per-host limits."""
        cached = self._page_cache.get(url)
        if cached is not None:
            return cached
        if not await is_public_url(url):
            return {"url": url, "content": "", "status": 0, "source": "blocked"}
        slot = host_slots.setdefault(hostname(url), asyncio.Semaphore(_HOST_CONCURRENCY))
        async with slot:
            try:
                page = await asyncio.wait_for(self._crawler.process_url(url), timeout=_CRAWL_TIMEOUT_S)
            except Exception as exc:  # timeout or crawler failure: one page never fails the search
                print(f"[JobSearchPlugin] Crawl failed for {hostname(url)}: {type(exc).__name__}", flush=True)
                page = {"url": url, "content": "", "status": 0, "source": "none"}
        if page.get("status") == 200:
            self._page_cache.set(url, page)
        return page

    async def _crawl_and_extract(
        self,
        first_wave: List[Candidate],
        llm_config: Optional[Dict[str, Any]],
        now: datetime.datetime,
        summary: JobSearchSummary,
        emit: EmitFn,
    ) -> Tuple[List[JobPosting], PageMap]:
        host_slots: Dict[str, asyncio.Semaphore] = {}
        pages: PageMap = {}
        jobs: List[JobPosting] = []
        unparsed: List[str] = []
        hints = {c.url: c.title for c in first_wave}
        seen_keys = {c.key for c in first_wave}
        total = len(first_wave)

        async def crawl(urls: List[str]) -> List[str]:
            """Crawl a wave; extract jobs as pages arrive. Returns posting links harvested from listings."""
            harvested: List[str] = []

            async def one(url: str) -> None:
                page = await self._fetch_page(url, host_slots)
                pages[url] = page
                if page.get("status") != 200:
                    summary.crawl_failures += 1
                else:
                    summary.crawled += 1
                    job = self._parse(url, page, now, hints.get(url, ""))
                    if job:
                        jobs.append(job)
                    else:
                        unparsed.append(url)
                        harvested.extend(harvest_posting_links(str(page.get("html") or ""),
                                                               str(page.get("final_url") or url), _HARVEST_PER_PAGE))
                await emit("job_extracted", "Deep Crawl", "running",
                           f"Crawled {summary.crawled + summary.crawl_failures}/{total} pages · {len(jobs)} jobs extracted.")

            await asyncio.gather(*(one(u) for u in urls))
            return harvested

        await emit("crawl_started", "Deep Crawl", "running",
                   f"Deep crawling top {total} candidates (trafilatura → markdownify → Playwright)...")
        harvested = await crawl([c.url for c in first_wave])

        second_wave: List[str] = []
        for link in harvested:
            key = dedup_key(link)
            if key not in seen_keys and len(first_wave) + len(second_wave) < _CRAWL_BUDGET:
                seen_keys.add(key)
                second_wave.append(link)
        if second_wave:
            total += len(second_wave)
            await emit("crawl_started", "Deep Crawl", "running",
                       f"Following {len(second_wave)} posting links found on listing pages...")
            await crawl(second_wave)

        if llm_config:
            jobs.extend(await self._llm_fallback(unparsed, pages, llm_config, now, hints))

        summary.extracted = len(jobs)
        await emit("job_extracted", "Deep Crawl", "completed",
                   f"Crawled {summary.crawled} pages ({summary.crawl_failures} unavailable) · {len(jobs)} jobs extracted.")
        return jobs, pages

    @staticmethod
    def _parse(url: str, page: Dict[str, Any], now: datetime.datetime, hint_title: str,
               overrides: Optional[Dict[str, str]] = None) -> Optional[JobPosting]:
        try:
            return parse_job_page(
                url=url,
                final_url=str(page.get("final_url") or url),
                html=str(page.get("html") or ""),
                markdown=str(page.get("content") or ""),
                crawl_tier=str(page.get("source") or "unknown"),
                now=now,
                hint_title=hint_title,
                overrides=overrides,
            )
        except Exception as exc:  # malformed markup must never abort the search
            print(f"[JobSearchPlugin] Extraction failed for {hostname(url)}: {type(exc).__name__}: {exc}", flush=True)
            return None

    async def _llm_fallback(
        self,
        urls: List[str],
        pages: PageMap,
        llm_config: Dict[str, Any],
        now: datetime.datetime,
        hints: Dict[str, str],
    ) -> List[JobPosting]:
        """Targeted LLM reading for a few pages deterministic extraction could not parse."""
        eligible = [
            u for u in urls
            if classify_url(u) != "listing" and len(str(pages[u].get("content") or "")) >= _LLM_MIN_TEXT_CHARS
        ][:_LLM_EXTRACT_MAX_PAGES]
        semaphore = asyncio.Semaphore(_LLM_EXTRACT_CONCURRENCY)

        async def one(url: str) -> Optional[JobPosting]:
            async with semaphore:
                fields = await llm_extract_job_fields(str(pages[url].get("content") or ""), llm_config)
            return self._parse(url, pages[url], now, hints.get(url, ""), overrides=fields) if fields else None

        return [job for job in await asyncio.gather(*(one(u) for u in eligible)) if job]

    # ── Application verification ──────────────────────────────────────────────

    async def _verify_jobs(self, jobs: List[JobPosting], pages: PageMap) -> None:
        semaphore = asyncio.Semaphore(_VERIFY_CONCURRENCY)

        async def one(job: JobPosting) -> None:
            async with semaphore:
                status, checked = await self._verify_application(job.application_url, pages)
            if checked:
                job.application_verified = status != "unknown"
                job.last_verified_at = utcnow()
            # A posting already flagged by its own page keeps the more severe status.
            if job.status in ("unknown", "active") or status == "closed":
                job.status = status

        await asyncio.gather(*(one(j) for j in jobs))

    async def _verify_application(self, url: Optional[str], pages: PageMap) -> Tuple[JobStatus, bool]:
        """Return ``(status, checked)``. ``checked`` is True only if a real HTTP answer was evaluated."""
        if not url:
            return "unknown", False
        page = pages.get(url)
        if page and page.get("status") == 200:
            # This exact page was fetched moments ago by the crawl — reuse it instead of re-requesting.
            return ("possibly_closed" if page_signals_closed(str(page.get("content") or "")) else "active"), True
        cached = self._verify_cache.get(url)
        if cached is not None:
            return cached
        result = await self._http_check(url)
        if result[1]:
            self._verify_cache.set(url, result)
        return result

    async def _http_check(self, url: str) -> Tuple[JobStatus, bool]:
        """Bounded GET of an application URL. Access restrictions (401/403/429) are never read as 'closed'."""
        if not await is_public_url(url):
            return "unknown", False
        async with httpx.AsyncClient(
            timeout=_VERIFY_TIMEOUT_S,
            follow_redirects=True,
            max_redirects=_VERIFY_MAX_REDIRECTS,
            headers=self._crawler.headers,
            event_hooks={"request": [guard_request]},
        ) as client:
            for attempt in range(_VERIFY_ATTEMPTS):
                last_attempt = attempt == _VERIFY_ATTEMPTS - 1
                try:
                    async with client.stream("GET", url) as response:
                        code = response.status_code
                        body = b""
                        if code == 200:
                            async for chunk in response.aiter_bytes():
                                body += chunk
                                if len(body) >= _VERIFY_MAX_BODY_BYTES:
                                    break
                        final_url = str(response.url)
                        encoding = response.encoding or "utf-8"
                except BlockedURLError:
                    return "unknown", False
                except httpx.HTTPError:
                    if last_attempt:
                        return "unknown", False
                    await asyncio.sleep(_VERIFY_BACKOFF_S * (2 ** attempt))
                    continue

                if code == 200:
                    text = visible_text(body.decode(encoding, errors="replace"))
                    closed = page_signals_closed(text) or is_generic_landing(url, final_url)
                    return ("possibly_closed" if closed else "active"), True
                if code == 410:
                    return "closed", True
                if code == 404 and last_attempt:
                    return "possibly_closed", True  # repeated 404 — likely removed, but not proven closed
                if code == 404 or code == 429 or code >= 500:
                    if not last_attempt:
                        await asyncio.sleep(_VERIFY_BACKOFF_S * (2 ** attempt))
                    continue
                return "unknown", False  # 401/403/999 etc.: access restricted, says nothing about the job
        return "unknown", False
