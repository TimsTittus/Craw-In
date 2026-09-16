"""
job_search_plugin/urls.py
─────────────────────────────────────────────────────────────────────────────
URL hygiene for untrusted job links:

  • canonicalisation (tracking parameters, fragments, trailing slashes)
  • source identification (known job boards and ATS platforms)
  • posting vs. listing classification and stable per-source job IDs
  • SSRF guarding — every fetched URL (including redirects) must resolve
    exclusively to public IP addresses on the default web ports
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal, Optional, Pattern, Tuple
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx

from backend.plugins.search_api_plugin.plugin import SimpleTTLCache

UrlKind = Literal["posting", "listing", "unknown"]


@dataclass(frozen=True)
class SourceProfile:
    """Static description of a job source. Adding a source is a data change, not a code change."""

    name: str
    domains: Tuple[str, ...]
    kind: Literal["job_board", "ats"]
    posting_pattern: Pattern[str]          # matched against "path?query"; group(1) is the stable job id
    site_query: Optional[str] = None       # "site:" target for discovery; None = never searched directly
    regions: Tuple[str, ...] = field(default_factory=tuple)  # () = global
    internship_focused: bool = False


SOURCE_PROFILES: Tuple[SourceProfile, ...] = (
    SourceProfile("LinkedIn", ("linkedin.com",), "job_board",
                  re.compile(r"^/jobs/view/(?:[^/?]*-)?(\d{6,})"), "linkedin.com/jobs"),
    SourceProfile("Indeed", ("indeed.com",), "job_board",
                  re.compile(r"^/(?:viewjob|rc/clk|m/viewjob)\?(?:.*&)?jk=([0-9a-f]{8,})"), "indeed.com/viewjob"),
    SourceProfile("Naukri", ("naukri.com",), "job_board",
                  re.compile(r"^/job-listings-[^?]*?-(\d{6,})"), "naukri.com/job-listings", regions=("india",)),
    SourceProfile("Internshala", ("internshala.com",), "job_board",
                  re.compile(r"^/(?:internship|job)/details?/[^?]*?(\d{5,})"), "internshala.com",
                  regions=("india",), internship_focused=True),
    SourceProfile("Foundit", ("foundit.in",), "job_board",
                  re.compile(r"^/job/[^?]*?-(\d{5,})"), None, regions=("india",)),
    SourceProfile("Wellfound", ("wellfound.com", "angel.co"), "job_board",
                  re.compile(r"^/(?:company/[^/]+/)?jobs/(\d{4,})"), "wellfound.com/jobs"),
    SourceProfile("Glassdoor", ("glassdoor.com", "glassdoor.co.in", "glassdoor.co.uk"), "job_board",
                  re.compile(r"(?:[?&]jl=|jobListingId=)(\d{6,})")),
    SourceProfile("Greenhouse", ("greenhouse.io",), "ats",
                  re.compile(r"^/[^/]+/jobs/(\d{4,})"), "greenhouse.io"),
    SourceProfile("Lever", ("lever.co",), "ats",
                  re.compile(r"^/[^/]+/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"), "jobs.lever.co"),
    SourceProfile("Ashby", ("ashbyhq.com",), "ats",
                  re.compile(r"^/[^/]+/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"), "jobs.ashbyhq.com"),
    SourceProfile("Workday", ("myworkdayjobs.com",), "ats",
                  re.compile(r"/job/[^?]+_([A-Za-z0-9-]{4,})/?(?:\?|$)"), "myworkdayjobs.com"),
    SourceProfile("SmartRecruiters", ("smartrecruiters.com",), "ats",
                  re.compile(r"^/[^/]+/(\d{6,})"), "jobs.smartrecruiters.com"),
    SourceProfile("Workable", ("workable.com",), "ats",
                  re.compile(r"^/[^/]+/j/([A-Z0-9]{6,})"), "apply.workable.com"),
)

# Sites that host discussion or media about jobs, never the postings themselves.
NON_JOB_DOMAINS: Tuple[str, ...] = (
    "youtube.com", "reddit.com", "quora.com", "wikipedia.org", "facebook.com", "instagram.com",
    "x.com", "twitter.com", "medium.com", "pinterest.com", "tiktok.com",
)

# Only parameters that never identify a job; anything unrecognised is kept so distinct jobs never collapse.
_TRACKING_PARAMS = frozenset({
    "gclid", "fbclid", "msclkid", "dclid", "yclid", "mc_cid", "mc_eid", "ref", "refid", "ref_src",
    "referer", "referrer", "trk", "trkinfo", "trackingid", "tracking_id", "lipi", "src", "sourceid",
    "_ga", "_gl", "gh_src", "lever-source", "lever-origin", "originalsubdomain", "hsctatracking",
})
_TRACKING_PREFIXES = ("utm_", "trk", "mc_", "_hs", "hsa_", "pk_")
_REDIRECT_PARAMS = ("url", "u", "redirect", "redirect_url", "dest", "destination", "target", "uddg", "q")
_LISTING_HINT = re.compile(r"/search\b|/jobs/?$|/jobs/search|/q-|-jobs(?:-in-|$|/)|/careers/?$|[?&](?:q|keywords?)=", re.I)
_POSTING_HINT = re.compile(r"/(?:jobs?|careers?|positions?|openings?|vacanc(?:y|ies)|requisitions?)/[^?#]*\d{3,}", re.I)
_SAFE_PORTS = (None, 80, 443)
_BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet")

_DNS_CACHE = SimpleTTLCache(ttl_seconds=300.0)
# Pure string → value helpers below run many times per URL during a search; memoise them.
_URL_MEMO_SIZE: int = 4_096


class BlockedURLError(httpx.RequestError):
    """Raised by the request guard when a URL targets a non-public destination."""


@lru_cache(maxsize=_URL_MEMO_SIZE)
def hostname(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _host_matches(host: str, domains: Tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


@lru_cache(maxsize=_URL_MEMO_SIZE)
def identify_source(url: str) -> Optional[SourceProfile]:
    host = hostname(url)
    return next((p for p in SOURCE_PROFILES if _host_matches(host, p.domains)), None)


def is_non_job_domain(url: str) -> bool:
    return _host_matches(hostname(url), NON_JOB_DOMAINS)


def unwrap_redirect(url: str) -> str:
    """Return the destination of a redirector link (…?url=https://real) — only if it is http(s)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        if key.lower() in _REDIRECT_PARAMS:
            candidate = unquote(value)
            if not candidate.startswith(("http://", "https://")):
                continue
            try:
                if urlsplit(candidate).hostname:
                    return candidate
            except ValueError:  # malformed target (e.g. "http://[bad") — not a usable redirect
                continue
    return url


def canonicalize_url(url: str) -> Optional[str]:
    """Normalise an http(s) URL for comparison. Returns None for anything else."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return None
    host = parts.hostname.lower().rstrip(".")
    netloc = host if port in _SAFE_PORTS else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    profile = identify_source(url)
    if profile and profile.posting_pattern.search(path):
        query: list = []  # the job id lives in the path, so the query string is only navigation/tracking noise
    else:
        query = sorted(
            (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
            if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith(_TRACKING_PREFIXES)
        )
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))


def _path_and_query(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}" if parts.query else parts.path


@lru_cache(maxsize=_URL_MEMO_SIZE)
def posting_id(url: str) -> Optional[str]:
    """Stable ``source:id`` key for a posting on a known platform (same job, different URL shapes)."""
    profile = identify_source(url)
    if not profile:
        return None
    match = profile.posting_pattern.search(_path_and_query(url))
    return f"{profile.name.lower()}:{match.group(1).lower()}" if match else None


@lru_cache(maxsize=_URL_MEMO_SIZE)
def dedup_key(url: str) -> str:
    """Level-1 deduplication key: platform job id when known, else the canonical URL without scheme."""
    job_id = posting_id(url)
    if job_id:
        return job_id
    canonical = canonicalize_url(url) or url
    parts = urlsplit(canonical)
    host = hostname(canonical)
    return f"{host}{parts.path}" + (f"?{parts.query}" if parts.query else "")


@lru_cache(maxsize=_URL_MEMO_SIZE)
def classify_url(url: str) -> UrlKind:
    """Is this URL a single posting, a search/listing page, or unclear?"""
    if posting_id(url):
        return "posting"
    path_query = _path_and_query(url)
    if identify_source(url) or _LISTING_HINT.search(path_query):
        return "listing"
    return "posting" if _POSTING_HINT.search(path_query) else "unknown"


_GENERIC_LANDING = re.compile(r"^/?(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:jobs|careers|search|job-search|positions)?/?$", re.I)


def is_generic_landing(original_url: str, final_url: str) -> bool:
    """True when a job link redirected to a generic careers/search page — a common sign of a removed posting."""
    original_path = urlsplit(original_url).path
    final_path = urlsplit(final_url).path
    return final_path != original_path and bool(_GENERIC_LANDING.match(final_path))


def ats_company_slug(url: str) -> Optional[str]:
    """Company identifier embedded in an ATS URL (boards.greenhouse.io/<slug>, <slug>.myworkdayjobs.com)."""
    profile = identify_source(url)
    if not profile or profile.kind != "ats":
        return None
    parts = urlsplit(url)
    if profile.name == "Workday":
        return (parts.hostname or "").split(".")[0] or None
    segments = [s for s in parts.path.split("/") if s]
    return segments[0] if segments else None


# ─────────────────────────────────────────────────────────────────────────────
# SSRF guard
# ─────────────────────────────────────────────────────────────────────────────

def _ip_is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def _resolve_host(host: str) -> Tuple[str, ...]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return tuple({str(info[4][0]) for info in infos})


async def is_public_url(url: str) -> bool:
    """True only for http(s) URLs on default ports whose host resolves exclusively to public IPs.

    Blocks localhost, private/link-local ranges (incl. cloud metadata 169.254.169.254),
    internal hostnames, embedded credentials and non-web ports.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme.lower() not in ("http", "https") or not host or port not in _SAFE_PORTS:
        return False
    if parts.username or parts.password or "." not in host or host == "localhost":
        return False
    if host.endswith(_BLOCKED_HOST_SUFFIXES):
        return False
    try:
        return _ip_is_public(str(ipaddress.ip_address(host)))
    except ValueError:
        pass  # not an IP literal → resolve below

    cached = _DNS_CACHE.get(host)
    if cached is not None:
        return cached
    try:
        addresses = await _resolve_host(host)
    except (OSError, UnicodeError):
        return False
    verdict = bool(addresses) and all(_ip_is_public(a) for a in addresses)
    _DNS_CACHE.set(host, verdict)
    return verdict


async def guard_request(request: httpx.Request) -> None:
    """httpx request hook: runs for the first request and for every redirect hop."""
    if not await is_public_url(str(request.url)):
        raise BlockedURLError(f"Blocked non-public URL: {request.url.host}", request=request)
