"""
job_search_plugin/extract.py
─────────────────────────────────────────────────────────────────────────────
Turns one crawled page into a normalised ``JobPosting``.

Extraction order (cheapest and most reliable first):
  1. schema.org ``JobPosting`` JSON-LD embedded in the page
  2. Deterministic heuristics (meta tags, title patterns, labelled fields)
  3. Grounded LLM field overrides supplied by the caller (see llm_assist.py)

Also detects application links, harvests posting links from listing pages and
recognises "this job is closed" notices. Nothing here performs network I/O.
"""

from __future__ import annotations

import datetime
import hashlib
import html as html_lib
import json
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import markdownify  # type: ignore
from bs4 import BeautifulSoup

from backend.plugins.job_search_plugin.normalize import (
    company_key,
    detect_work_mode,
    find_skills,
    infer_level,
    normalize_employment_type,
    parse_date,
    parse_experience,
    parse_relative_posted,
)
from backend.plugins.job_search_plugin.schema import ApplicationType, JobPosting, JobSourceRef, SourceKind
from backend.plugins.job_search_plugin.urls import (
    SourceProfile,
    ats_company_slug,
    canonicalize_url,
    classify_url,
    dedup_key,
    hostname,
    identify_source,
    is_non_job_domain,
    posting_id,
    unwrap_redirect,
)

_DESCRIPTION_MAX_CHARS: int = 6_000
_HEADER_SCAN_CHARS: int = 1_500
_CLOSED_SCAN_CHARS: int = 20_000
_MIN_JOB_TEXT_CHARS: int = 200
_SECTION_MAX_ITEMS: int = 12
_SECTION_ITEM_MAX_CHARS: int = 240
_MAX_SKILLS: int = 25
_MAX_LOCATIONS: int = 6
_MAX_APPLY_CANDIDATES: int = 10

APPLICATION_TYPE_RANK: Dict[ApplicationType, int] = {
    "direct_company": 4, "ats": 3, "external": 2, "job_board": 1, "unknown": 0,
}

_APPLY_TEXT = re.compile(r"\bapply\b|submit\s+(?:your\s+)?application|start\s+(?:your\s+)?application", re.I)
_APPLY_STRONG = re.compile(r"company(?:'s)?\s+(?:website|site)|apply\s+now|apply\s+for\s+this|submit\s+application", re.I)
_AUTH_PATH = re.compile(
    r"/(?:login|log-in|signin|sign-in|signup|sign-up|register|join|authwall|uas/login|checkpoint)(?:[/?#]|$)", re.I
)
_CLOSED_TEXT = re.compile(
    r"no\s+longer\s+(?:accepting\s+applications|available|open|active|being\s+accepted)"
    r"|(?:position|job|role|vacancy|posting)\s+(?:has\s+been\s+filled|has\s+(?:been\s+)?closed|has\s+expired|is\s+closed|is\s+expired)"
    r"|this\s+(?:job|posting|position|role)\s+(?:has\s+)?(?:expired|closed)"
    r"|applications?\s+(?:are\s+|is\s+)?(?:now\s+)?closed",
    re.I,
)
_JOB_SIGNALS = re.compile(
    r"responsibilit|qualification|requirement|experience|job\s+description|what\s+you(?:'|’)?ll\s+do|about\s+the\s+role|\bapply\b",
    re.I,
)
_LABELLED_LOCATION = re.compile(
    r"(?:^|\n)\s*[*_]*\s*(?:job\s+)?locations?\s*[*_]*\s*[:\-–]\s*[*_]*\s*([^\n|*]{2,80})", re.I
)
_LABELLED_EMPLOYMENT = re.compile(
    r"(?:employment|job|work|position|contract)\s+type\s*[*_]*\s*[:\-–]?\s*[*_]*\s*([A-Za-z][A-Za-z \-]{2,30})", re.I
)
_LABELLED_WORK_MODE = re.compile(
    r"(?:workplace|work|location|job)\s+(?:type|mode|model|arrangement)\s*[*_]*\s*[:\-–]\s*[*_]*\s*([A-Za-z][A-Za-z \-]{2,30})",
    re.I,
)
_LINKEDIN_TITLE = re.compile(
    r"^(?P<company>.+?)\s+hiring\s+(?P<title>.+?)\s+in\s+(?P<location>.+?)(?:\s*\|\s*LinkedIn)?$", re.I
)
_TITLE_AT_COMPANY = re.compile(
    r"^(?:job\s+application\s+for\s+)?(?P<title>.+?)\s+(?:at|@)\s+(?P<company>[^|–—]+?)\s*$", re.I
)
_BOARD_SUFFIX = re.compile(
    r"\s*[|–—-]\s*(?:LinkedIn|Indeed(?:\.com)?|Glassdoor|Naukri(?:\.com)?|Internshala|Wellfound|Foundit|Jobs?)\s*$", re.I
)
_TITLE_SPLIT = re.compile(r"\s+[|–—]\s+|\s+-\s+")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_HEADING = re.compile(r"^#\s+(.{2,200})$", re.M)
_HEADING_HINT = re.compile(r"^(?:#{1,6}\s+.+|\*\*[^*]{2,80}\*\*:?|__[^_]{2,80}__:?|[^.!?]{2,60}:)$")
_REQUIREMENTS_HEADING = re.compile(
    r"requirement|qualification|what\s+you(?:'|’)?ll\s+need|must[\s-]have|looking\s+for|who\s+you\s+are"
    r"|skills|eligibility|you\s+have|about\s+you",
    re.I,
)
_PREFERRED_HEADING = re.compile(r"prefer|nice[\s-]to[\s-]have|bonus|good[\s-]to[\s-]have|\bplus\b|desirable", re.I)
# Titles of roundup/marketing pages rather than one posting ("Best Cyber Security Internships (2025)").
_LISTING_TITLE = re.compile(
    r"^(?:best|top|\d+\+?)\b|\b(?:jobs|internships|openings|vacancies|careers|opportunities|interns)\b", re.I
)
_URL_LIKE = re.compile(r"^(?:https?://|www\.)", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•●▪◦]|\d{1,2}[.)])\s+(?P<text>.+)$")


def make_job_id(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def visible_text(html: str) -> str:
    """Human-visible text of an HTML document (scripts and styles removed)."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def page_signals_closed(text: str) -> bool:
    """True when the page says the job is no longer open."""
    return bool(_CLOSED_TEXT.search((text or "")[:_CLOSED_SCAN_CHARS]))


# ─────────────────────────────────────────────────────────────────────────────
# schema.org JobPosting (JSON-LD)
# ─────────────────────────────────────────────────────────────────────────────

def _iter_ld_nodes(data: Any) -> Iterator[Dict[str, Any]]:
    if isinstance(data, list):
        for item in data:
            yield from _iter_ld_nodes(item)
    elif isinstance(data, dict):
        yield data
        if "@graph" in data:
            yield from _iter_ld_nodes(data["@graph"])


def _find_job_posting(soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
    for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw, strict=False)
        except ValueError:
            continue
        for node in _iter_ld_nodes(data):
            types = node.get("@type")
            if "JobPosting" in (types if isinstance(types, list) else [types]):
                return node
    return None


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _ld_str(value: Any) -> Optional[str]:
    if isinstance(value, str):
        text = " ".join(html_lib.unescape(value).split())
        return text or None
    if isinstance(value, dict):
        return _ld_str(value.get("name") or value.get("@value"))
    if isinstance(value, list):
        return next((s for s in (_ld_str(v) for v in value) if s), None)
    return None


def _ld_number(value: Any) -> Optional[float]:
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _ld_locations(node: Dict[str, Any]) -> List[str]:
    places: List[str] = []
    for loc in _as_list(node.get("jobLocation")):
        address = loc.get("address", loc) if isinstance(loc, dict) else loc
        if isinstance(address, str):
            places.append(address)
        elif isinstance(address, dict):
            parts = [_ld_str(address.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry")]
            joined = ", ".join(dict.fromkeys(p for p in parts if p))
            if joined:
                places.append(joined)
    for requirement in _as_list(node.get("applicantLocationRequirements")):
        name = _ld_str(requirement)
        if name:
            places.append(name)
    return list(dict.fromkeys(" ".join(p.split()) for p in places if p and p.strip()))[:_MAX_LOCATIONS]


def _ld_salary(node: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
    salary = next((s for s in _as_list(node.get("baseSalary") or node.get("estimatedSalary")) if isinstance(s, dict)), None)
    if salary is None:
        return None, None, None, None
    value = salary.get("value")
    if isinstance(value, dict):
        low, high = _ld_number(value.get("minValue")), _ld_number(value.get("maxValue"))
        single, period = _ld_number(value.get("value")), _ld_str(value.get("unitText"))
    else:
        low = high = None
        single, period = _ld_number(value), _ld_str(salary.get("unitText"))
    if single and low is None and high is None:
        low = high = single
    if not low and not high:
        return None, None, None, None
    currency = _ld_str(salary.get("currency"))
    return low or None, high or None, currency.upper() if currency else None, period.lower() if period else None


def _ld_experience(node: Dict[str, Any]) -> Optional[Tuple[float, Optional[float]]]:
    requirement = node.get("experienceRequirements")
    if isinstance(requirement, dict):
        months = _ld_number(requirement.get("monthsOfExperience"))
        if months is not None:
            return round(months / 12, 1), None
        requirement = requirement.get("description")
    if isinstance(requirement, str):
        return parse_experience(requirement, require_context=False)
    return None


def _html_to_markdown(fragment: str) -> str:
    text = fragment if "<" in fragment else html_lib.unescape(fragment)
    if "<" not in text:
        return text.strip()
    return markdownify.markdownify(text, heading_style="ATX").strip()


# ─────────────────────────────────────────────────────────────────────────────
# Heuristics
# ─────────────────────────────────────────────────────────────────────────────

def _meta(soup: BeautifulSoup, *names: str) -> Optional[str]:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        content = tag.get("content") if tag else None
        if isinstance(content, str) and content.strip():
            return " ".join(content.split())
    return None


def _page_title(soup: Optional[BeautifulSoup], markdown: str, hint_title: str) -> str:
    """og:title → <h1> → first Markdown heading (JS-rendered pages) → <title> → search result title."""
    if soup is not None:
        meta = _meta(soup, "og:title", "twitter:title")
        if meta:
            return meta
        heading = soup.find("h1")
        if heading and heading.get_text(strip=True):
            return " ".join(heading.get_text(" ", strip=True).split())
    rendered = _MD_HEADING.search(markdown[:_HEADER_SCAN_CHARS])
    if rendered:
        return _clean_inline(rendered.group(1))
    if soup is not None:
        title_tag = soup.find("title")
        if title_tag and title_tag.get_text(strip=True):
            return " ".join(title_tag.get_text(" ", strip=True).split())
    return hint_title


def _split_title(raw_title: str, page_url: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Split page titles like 'Acme hiring Analyst in Pune | LinkedIn' into (title, company, location)."""
    raw = " ".join(raw_title.split())
    linkedin = _LINKEDIN_TITLE.match(raw)
    if linkedin:
        return linkedin["title"], linkedin["company"], linkedin["location"]
    raw = _BOARD_SUFFIX.sub("", raw)
    at_company = _TITLE_AT_COMPANY.match(raw.split(" | ")[0])
    if at_company:
        return at_company["title"], at_company["company"], None
    segments = [s.strip() for s in _TITLE_SPLIT.split(raw) if s.strip()]
    slug = ats_company_slug(page_url)
    if slug and len(segments) >= 2:
        slug_key = company_key(slug.replace("-", " ")).replace(" ", "")
        for i, segment in enumerate(segments):
            if slug_key and company_key(segment).replace(" ", "") == slug_key:
                rest = [s for j, s in enumerate(segments) if j != i]
                return rest[0], segment, None
    return (segments[0] if segments else raw), None, None


def _clean_inline(text: str) -> str:
    return " ".join(_MD_LINK.sub(r"\1", text).replace("**", "").replace("__", "").split())


def _extract_sections(markdown: str) -> Tuple[List[str], List[str]]:
    """Bullet lists under 'Requirements'/'Qualifications' and 'Preferred'/'Nice to have' headings."""
    requirements: List[str] = []
    preferred: List[str] = []
    current: Optional[List[str]] = None
    for line in markdown.splitlines():
        bullet = _BULLET.match(line)
        if bullet:
            if current is not None and len(current) < _SECTION_MAX_ITEMS:
                item = _clean_inline(bullet.group("text"))[:_SECTION_ITEM_MAX_CHARS]
                if item:
                    current.append(item)
            continue
        stripped = line.strip()
        if stripped and _HEADING_HINT.match(stripped):
            if _PREFERRED_HEADING.search(stripped):
                current = preferred
            elif _REQUIREMENTS_HEADING.search(stripped):
                current = requirements
            else:
                current = None
    return requirements, preferred


# ─────────────────────────────────────────────────────────────────────────────
# Application links
# ─────────────────────────────────────────────────────────────────────────────

def classify_application_url(url: str, company: Optional[str]) -> ApplicationType:
    profile = identify_source(url)
    if profile:
        return "ats" if profile.kind == "ats" else "job_board"
    host = hostname(url)
    company_tokens = [t for t in company_key(company).split() if len(t) >= 3]
    if company_tokens and any(t in host for t in company_tokens):
        return "direct_company"
    return "external"


def _application_candidates(soup: BeautifulSoup, page_url: str, page_profile: Optional[SourceProfile]) -> List[Tuple[str, int]]:
    """Links that look like 'Apply' actions, scored by how strongly they point to an application."""
    scored: Dict[str, int] = {}
    unlabelled_ats: Dict[str, int] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        target = canonicalize_url(unwrap_redirect(urljoin(page_url, href)))
        if not target or _AUTH_PATH.search(urlsplit(target).path):
            continue
        label = " ".join(filter(None, (anchor.get_text(" ", strip=True), anchor.get("aria-label"), anchor.get("title"))))[:160]
        target_profile = identify_source(target)
        is_ats_posting = bool(target_profile and target_profile.kind == "ats" and posting_id(target))
        if _APPLY_TEXT.search(label):
            score = 1 + (2 if _APPLY_STRONG.search(label) else 0) + (3 if is_ats_posting else 0)
            if target == page_url:
                score -= 2
            scored[target] = max(scored.get(target, score), score)
        elif is_ats_posting and not (page_profile and page_profile.kind == "ats"):
            unlabelled_ats[target] = 3
    # A single unlabelled ATS posting link on a board/company page is the application;
    # several of them are just "other openings" and prove nothing.
    if len(unlabelled_ats) == 1:
        scored.update(unlabelled_ats)
    return sorted(scored.items(), key=lambda kv: -kv[1])[:_MAX_APPLY_CANDIDATES]


def _choose_application(
    page_url: str, candidates: List[Tuple[str, int]], company: Optional[str]
) -> Tuple[Optional[str], ApplicationType]:
    """Prefer company/ATS application links over the listing page itself."""
    page_type = classify_application_url(page_url, company)
    best: Optional[Tuple[Tuple[int, int], str, ApplicationType]] = None
    for url, score in candidates:
        app_type = classify_application_url(url, company)
        rank = (APPLICATION_TYPE_RANK[app_type], score)
        if best is None or rank > best[0]:
            best = (rank, url, app_type)
    if best and best[1] != page_url and best[0][0] >= APPLICATION_TYPE_RANK[page_type]:
        return best[1], best[2]
    # No better link: the posting page itself is where applicants apply (ATS forms, board apply buttons).
    return page_url, page_type


def harvest_posting_links(html: str, base_url: str, limit: int) -> List[str]:
    """Individual posting URLs linked from a listing/search page."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links: Dict[str, str] = {}
    for anchor in soup.find_all("a", href=True):
        url = canonicalize_url(unwrap_redirect(urljoin(base_url, str(anchor["href"]))))
        if url and not is_non_job_domain(url) and classify_url(url) == "posting":
            links.setdefault(dedup_key(url), url)
        if len(links) >= limit:
            break
    return list(links.values())


# ─────────────────────────────────────────────────────────────────────────────
# Page → JobPosting
# ─────────────────────────────────────────────────────────────────────────────

def _source_ref(page_url: str, profile: Optional[SourceProfile], company: Optional[str]) -> JobSourceRef:
    if profile:
        return JobSourceRef(name=profile.name, url=page_url, kind=profile.kind)
    kind: SourceKind = "company" if classify_application_url(page_url, company) == "direct_company" else "web"
    return JobSourceRef(name=hostname(page_url), url=page_url, kind=kind)


def parse_job_page(
    *,
    url: str,
    final_url: str,
    html: str,
    markdown: str,
    crawl_tier: str,
    now: datetime.datetime,
    hint_title: str = "",
    overrides: Optional[Dict[str, str]] = None,
) -> Optional[JobPosting]:
    """Build a JobPosting from a crawled page, or return None if the page is not a single job."""
    page_url = canonicalize_url(final_url or url) or url
    profile = identify_source(page_url)
    soup = BeautifulSoup(html, "html.parser") if html else None
    node = _find_job_posting(soup) if soup is not None else None
    overrides = overrides or {}
    text = markdown or ""
    header = text[:_HEADER_SCAN_CHARS]
    raw: Dict[str, Any] = {"crawl_tier": crawl_tier}

    salary: Tuple[Optional[float], Optional[float], Optional[str], Optional[str]] = (None, None, None, None)
    experience: Optional[Tuple[float, Optional[float]]] = None
    valid_through: Optional[datetime.datetime] = None
    declared_skills: List[str] = []
    remote = None

    if node is not None:
        method = "json_ld"
        title = _ld_str(node.get("title"))
        company = _ld_str(node.get("hiringOrganization"))
        locations = _ld_locations(node)
        employment_raw = ", ".join(str(v) for v in _as_list(node.get("employmentType")))
        if "TELECOMMUTE" in str(node.get("jobLocationType", "")).upper():
            remote = "remote"
        posted_at, posted_estimated = parse_date(node.get("datePosted")), False
        valid_through = parse_date(node.get("validThrough"))
        salary = _ld_salary(node)
        experience = _ld_experience(node)
        description = _html_to_markdown(_ld_str(node.get("description")) or "") or text
        declared_skills = find_skills(" ".join(str(s) for s in _as_list(node.get("skills"))))
        raw.update(date_posted=node.get("datePosted"), valid_through=node.get("validThrough"))
    else:
        if classify_url(page_url) == "listing" and not overrides:
            return None
        method = "heuristic"
        title, company, title_location = _split_title(_page_title(soup, text, hint_title), page_url)
        if not company and profile is None and soup is not None:
            company = _meta(soup, "og:site_name")
        locations = [title_location] if title_location else []
        if not locations:
            labelled = _LABELLED_LOCATION.search(header)
            if labelled:
                locations = [_clean_inline(labelled.group(1))]
        employment_match = _LABELLED_EMPLOYMENT.search(header)
        employment_raw = employment_match.group(1) if employment_match else ""
        posted_at = parse_relative_posted(header, now)
        posted_estimated = posted_at is not None
        description = text

    if overrides:
        method = "llm_assisted"
        title = title or overrides.get("title")
        company = company or overrides.get("company")
        if not locations and overrides.get("location"):
            locations = [overrides["location"]]

    title = _clean_inline(title or "")[:200]
    if not title:
        return None
    if company and _URL_LIKE.match(company):
        company = None
    if method == "heuristic" and (
        len(text) < _MIN_JOB_TEXT_CHARS or not _JOB_SIGNALS.search(text) or _LISTING_TITLE.search(title)
    ):
        return None

    location_text = "; ".join(locations)
    labelled_mode = _LABELLED_WORK_MODE.search(header)
    remote = (
        remote
        or detect_work_mode(location_text)
        or detect_work_mode(title)
        or (detect_work_mode(labelled_mode.group(1)) if labelled_mode else None)
    )
    employment = normalize_employment_type(employment_raw) or normalize_employment_type(title)
    description = description[:_DESCRIPTION_MAX_CHARS]
    experience = experience or parse_experience(description)
    exp_min, exp_max = experience if experience else (None, None)
    requirements, preferred = _extract_sections(description)
    skills = list(dict.fromkeys(declared_skills + find_skills(f"{title}\n{description}")))[:_MAX_SKILLS]

    if valid_through and valid_through < now:
        status = "closed"
    elif page_signals_closed(header):
        status = "possibly_closed"
    else:
        status = "unknown"

    candidates = _application_candidates(soup, page_url, profile) if soup is not None else []
    # Without structured data, an unknown page must look like ONE posting: a posting-shaped URL, or
    # an apply action plus at least one concrete job field. Course ads and roundups fail this.
    if method == "heuristic" and classify_url(page_url) != "posting" and not (
        candidates and (locations or employment_raw or posted_at)
    ):
        return None
    application_url, application_type = _choose_application(page_url, candidates, company)

    raw.update(employment_type=employment_raw or None, location=location_text or None,
               experience=[exp_min, exp_max] if experience else None)
    return JobPosting(
        job_id=make_job_id(dedup_key(page_url)),
        title=title,
        company=_clean_inline(company)[:120] if company else None,
        locations=locations,
        remote=remote,
        employment_type=employment,
        experience_level=infer_level(title, exp_min, employment),
        experience_min_years=exp_min,
        experience_max_years=exp_max,
        skills=skills,
        salary_min=salary[0],
        salary_max=salary[1],
        salary_currency=salary[2],
        salary_period=salary[3],
        description=description,
        requirements=requirements,
        preferred_qualifications=preferred,
        posted_at=posted_at,
        posted_at_estimated=posted_estimated,
        valid_through=valid_through,
        source=profile.name if profile else hostname(page_url),
        source_url=page_url,
        sources=[_source_ref(page_url, profile, company)],
        application_url=application_url,
        application_type=application_type,
        discovered_at=now,
        status=status,
        extraction_method=method,
        raw=raw,
    )
