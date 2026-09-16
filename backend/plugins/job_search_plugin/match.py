"""
job_search_plugin/match.py
─────────────────────────────────────────────────────────────────────────────
Deterministic relevance logic:

  • Candidate pre-ranking — cheap scoring of search results (title/snippet/URL)
                            to decide which few pages deserve a deep crawl
  • Deduplication         — L1 platform id / canonical URL / shared ATS posting,
                            L2 company+title+location fingerprint,
                            L3 same company + near-identical title + similar description
  • Ranking               — weighted, explainable score with reasons and concerns
"""

from __future__ import annotations

import datetime
import difflib
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from backend.plugins.job_search_plugin.extract import APPLICATION_TYPE_RANK
from backend.plugins.job_search_plugin.normalize import (
    canonical_location,
    company_key,
    detect_work_mode,
    is_india_location,
    location_matches,
    normalize_phrase,
    skill_in_text,
    title_key,
    tokens,
)
from backend.plugins.job_search_plugin.schema import ExperienceLevel, JobPosting, JobSearchQuery
from backend.plugins.job_search_plugin.urls import UrlKind, dedup_key, hostname, identify_source, posting_id

# Share of the final score per component. Components the user gave no criterion for are
# dropped and the remaining weights renormalised, so the score always reflects stated needs.
WEIGHTS: Dict[str, float] = {
    "skills": 0.35,
    "title": 0.20,
    "location": 0.15,
    "experience": 0.10,
    "freshness": 0.10,
    "employment": 0.05,
    "application": 0.05,
}
UNKNOWN_SCORE: float = 0.5            # job data missing for an active criterion
UNKNOWN_FRESHNESS_SCORE: float = 0.4  # undated postings rank slightly below dated ones
QUALIFY_THRESHOLD: int = 40           # minimum match_score (0-100) to be returned
DEFAULT_FRESHNESS_WINDOW_DAYS: int = 30
MIN_PRERANK_SCORE: float = 1.0        # candidates below this are never crawled

_MIN_TOKEN_CONTAINMENT: int = 5
_TITLE_SIMILARITY_MIN: float = 0.9
_DESCRIPTION_SIMILARITY_MIN: float = 0.5
_MIN_TOKENS_FOR_SIMILARITY: int = 80
_SHINGLE_SIZE: int = 3
_SHINGLE_MAX_TOKENS: int = 600
_EXPERIENCE_GAP_TOLERANCE_YEARS: float = 3.0
_MAX_MERGED_SKILLS: int = 25
_MAX_MERGED_LOCATIONS: int = 6

_LEVEL_YEARS: Dict[ExperienceLevel, Tuple[float, Optional[float]]] = {
    "internship": (0, 1), "entry": (0, 2), "mid": (2, 5), "senior": (5, 10), "lead": (8, None),
}
_EMPLOYMENT_LABELS = {
    "full_time": "Full-time", "part_time": "Part-time", "contract": "Contract",
    "internship": "Internship", "temporary": "Temporary",
}
_APPLICATION_BASE = {"direct_company": 0.85, "ats": 0.85, "job_board": 0.6, "external": 0.5, "unknown": 0.2}
_STATUS_SEVERITY = {"unknown": 0, "active": 1, "possibly_closed": 2, "closed": 3}
_INDIA_COUNTRY_LABELS = frozenset({"in", "ind", "india"})

_JOBISH = re.compile(r"job|career|hiring|vacanc|opening|intern|position|apply|recruit|employment", re.I)
_NO_SPONSORSHIP = re.compile(
    r"no\s+(?:visa\s+)?sponsorship|not\s+(?:able\s+to\s+)?(?:provide|offer)\s+(?:visa\s+)?sponsorship"
    r"|unable\s+to\s+sponsor|without\s+(?:visa\s+)?sponsorship|must\s+(?:already\s+)?have\s+(?:the\s+)?"
    r"(?:right|authori[sz]ation)\s+to\s+work",
    re.I,
)
_SPONSORSHIP_OFFERED = re.compile(
    r"visa\s+sponsorship\s+(?:is\s+)?(?:available|provided|offered)|will\s+sponsor|sponsorship\s+(?:is\s+)?available", re.I
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared matching primitives
# ─────────────────────────────────────────────────────────────────────────────

def _token_hit(token: str, bag: List[str]) -> bool:
    """Exact token match, or containment for long tokens (cybersecurity ⊇ security, internship ⊇ intern)."""
    return any(
        token == other
        or (len(token) >= _MIN_TOKEN_CONTAINMENT and len(other) >= _MIN_TOKEN_CONTAINMENT and (token in other or other in token))
        for other in bag
    )


def role_match(role: str, title: str) -> float:
    """Share of the role's words present in the title (1.0 when the phrase appears verbatim)."""
    role_tokens, title_tokens = tokens(role), tokens(title)
    if not role_tokens or not title_tokens:
        return 0.0
    if normalize_phrase(role) in normalize_phrase(title):
        return 1.0
    return sum(_token_hit(t, title_tokens) for t in role_tokens) / len(role_tokens)


def _search_terms(spec: JobSearchQuery) -> List[str]:
    return spec.roles or spec.keywords


# ─────────────────────────────────────────────────────────────────────────────
# Candidate pre-ranking (search-result level, no crawling)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    url: str
    key: str
    title: str
    snippet: str
    kind: UrlKind
    hits: int = 1
    score: float = 0.0


def looks_like_job_candidate(url: str, title: str) -> bool:
    """Cheap filter: known job sources pass; unknown sites need job vocabulary in URL or title."""
    return identify_source(url) is not None or bool(_JOBISH.search(f"{url} {title}"))


def prerank(candidates: List[Candidate], spec: JobSearchQuery) -> List[Candidate]:
    """Score candidates from title/snippet/URL alone and return them best-first."""
    terms = _search_terms(spec)
    wants_internship = "internship" in spec.employment_types or spec.experience_level == "internship"
    for c in candidates:
        text = f"{c.title} {c.snippet}"
        title_score = max((role_match(t, c.title) for t in terms), default=0.0)
        text_score = max((role_match(t, text) for t in terms), default=0.0)
        skill_hits = sum(skill_in_text(s, text) for s in spec.skills[:6])
        location_hit = any(location_matches(loc, text) for loc in spec.locations) or (
            spec.remote in ("remote_ok", "remote_only") and detect_work_mode(text) == "remote"
        )
        profile = identify_source(c.url)
        c.score = (
            3.0 * title_score
            + 1.0 * text_score
            + 0.5 * min(skill_hits, 4)
            + (1.0 if location_hit else 0.0)
            + {"posting": 2.0, "unknown": 0.0, "listing": -0.5}[c.kind]
            + (1.0 if profile and profile.kind == "ats" else 0.0)
            + 0.5 * min(c.hits - 1, 2)
            + (0.5 if wants_internship and "intern" in text.lower() else 0.0)
        )
    return sorted(candidates, key=lambda c: c.score, reverse=True)


def select_for_crawl(ranked: List[Candidate], limit: int, per_host: int) -> List[Candidate]:
    """Top candidates worth crawling, capped per host to spread load and respect source rate limits."""
    selected: List[Candidate] = []
    per_host_count: Dict[str, int] = {}
    for c in ranked:
        if c.score < MIN_PRERANK_SCORE or len(selected) >= limit:
            break
        host = hostname(c.url)
        if per_host_count.get(host, 0) >= per_host:
            continue
        per_host_count[host] = per_host_count.get(host, 0) + 1
        selected.append(c)
    return selected


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication
# ─────────────────────────────────────────────────────────────────────────────

def _completeness(job: JobPosting) -> int:
    filled = sum(v is not None for v in (
        job.company, job.remote, job.employment_type, job.experience_min_years, job.posted_at, job.salary_min,
    ))
    return filled + len(job.locations[:1]) + (3 if job.extraction_method == "json_ld" else 0) + APPLICATION_TYPE_RANK[job.application_type]


def _primary_place(job: JobPosting) -> Optional[str]:
    if job.remote == "remote":
        return "remote"
    return canonical_location(job.locations[0].split(",")[0]) if job.locations else None


def _fingerprint(job: JobPosting) -> Optional[str]:
    company, place = company_key(job.company), _primary_place(job)
    if not company or not place:
        return None  # unknown company or location → not enough evidence to merge on a fingerprint
    return f"fp:{company}|{title_key(job.title)}|{place}"


def _identity_keys(job: JobPosting) -> Set[str]:
    keys = {dedup_key(job.source_url)} | {dedup_key(s.url) for s in job.sources}
    if job.application_url and posting_id(job.application_url):
        keys.add(dedup_key(job.application_url))  # two boards pointing to the same ATS posting
    fingerprint = _fingerprint(job)
    if fingerprint:
        keys.add(fingerprint)
    return keys


def _shingles(text: str) -> Set[Tuple[str, ...]]:
    words = tokens(text)[:_SHINGLE_MAX_TOKENS]
    return {tuple(words[i:i + _SHINGLE_SIZE]) for i in range(max(0, len(words) - _SHINGLE_SIZE + 1))}


def description_similarity(a: str, b: str) -> Optional[float]:
    """Jaccard similarity of word 3-shingles; None when either text is too short to judge."""
    if len(tokens(a)) < _MIN_TOKENS_FOR_SIMILARITY or len(tokens(b)) < _MIN_TOKENS_FOR_SIMILARITY:
        return None
    sa, sb = _shingles(a), _shingles(b)
    return len(sa & sb) / len(sa | sb) if sa and sb else None


def _locations_compatible(a: JobPosting, b: JobPosting) -> bool:
    if not a.locations or not b.locations:
        return True
    a_text, b_text = "; ".join(a.locations), "; ".join(b.locations)
    return any(location_matches(loc.split(",")[0], b_text) for loc in a.locations) or any(
        location_matches(loc.split(",")[0], a_text) for loc in b.locations
    )


def _is_near_duplicate(a: JobPosting, b: JobPosting) -> bool:
    """Level 3: same company, near-identical title, compatible location AND similar description."""
    if not company_key(a.company) or company_key(a.company) != company_key(b.company):
        return False
    if difflib.SequenceMatcher(None, title_key(a.title), title_key(b.title)).ratio() < _TITLE_SIMILARITY_MIN:
        return False
    if not _locations_compatible(a, b):
        return False
    similarity = description_similarity(a.description, b.description)
    return similarity is not None and similarity >= _DESCRIPTION_SIMILARITY_MIN


def _merge(primary: JobPosting, other: JobPosting) -> JobPosting:
    """Fold ``other`` into ``primary``: fill gaps, keep every source, keep the best application link."""
    merged = primary.model_copy(deep=True)
    for field_name in ("company", "remote", "employment_type", "experience_level", "experience_min_years",
                       "experience_max_years", "salary_min", "salary_max", "salary_currency", "salary_period",
                       "valid_through"):
        if getattr(merged, field_name) is None and getattr(other, field_name) is not None:
            setattr(merged, field_name, getattr(other, field_name))
    if other.posted_at and (merged.posted_at is None or (merged.posted_at_estimated and not other.posted_at_estimated)):
        merged.posted_at, merged.posted_at_estimated = other.posted_at, other.posted_at_estimated

    seen_places = {canonical_location(loc) for loc in merged.locations}
    for loc in other.locations:
        if canonical_location(loc) not in seen_places and len(merged.locations) < _MAX_MERGED_LOCATIONS:
            merged.locations.append(loc)
            seen_places.add(canonical_location(loc))
    merged.skills = list(dict.fromkeys(merged.skills + other.skills))[:_MAX_MERGED_SKILLS]
    if len(other.description) > len(merged.description) and not merged.requirements:
        merged.description = other.description
    merged.requirements = merged.requirements or other.requirements
    merged.preferred_qualifications = merged.preferred_qualifications or other.preferred_qualifications

    known_sources = {dedup_key(s.url) for s in merged.sources}
    merged.sources.extend(s for s in other.sources if dedup_key(s.url) not in known_sources)

    if APPLICATION_TYPE_RANK[other.application_type] > APPLICATION_TYPE_RANK[merged.application_type]:
        merged.application_url, merged.application_type = other.application_url, other.application_type
    if _STATUS_SEVERITY[other.status] > _STATUS_SEVERITY[merged.status]:
        merged.status = other.status
    merged.raw = {**merged.raw, "merged_sources": len(merged.sources)}
    return merged


def merge_duplicates(jobs: List[JobPosting]) -> Tuple[List[JobPosting], int]:
    """Collapse the same job seen on several sources. Returns ``(unique_jobs, duplicates_removed)``."""
    groups: List[JobPosting] = []
    index: Dict[str, int] = {}
    removed = 0
    for job in sorted(jobs, key=_completeness, reverse=True):
        keys = _identity_keys(job)
        position = next((index[k] for k in keys if k in index), None)
        if position is None:
            position = next((i for i, g in enumerate(groups) if _is_near_duplicate(g, job)), None)
        if position is None:
            groups.append(job)
            position = len(groups) - 1
        else:
            groups[position] = _merge(groups[position], job)
            removed += 1
        for key in keys | _identity_keys(groups[position]):
            index[key] = position
    return groups, removed


# ─────────────────────────────────────────────────────────────────────────────
# Ranking
# ─────────────────────────────────────────────────────────────────────────────

Component = Tuple[float, Optional[str], Optional[str]]  # (score, reason, concern)


def _fmt_years(low: float, high: Optional[float]) -> str:
    if high is None:
        return f"{low:g}+ years"
    return f"{low:g}–{high:g} years" if high != low else f"{low:g} years"


def _skills_component(job: JobPosting, spec: JobSearchQuery) -> Tuple[float, List[str], List[str]]:
    job_text = f"{job.title}\n{' '.join(job.skills)}\n{job.description}"
    if len(job.description) < 200 and not job.skills:
        return UNKNOWN_SCORE, [], ["Skill requirements not stated"]
    matched = [s for s in spec.skills if s in job.skills or skill_in_text(s, job_text)]
    missing = [s for s in spec.skills if s not in matched]
    concerns = [f"Not mentioned: {', '.join(missing[:4])}"] if missing and matched else (
        ["None of your skills are mentioned"] if missing else []
    )
    return len(matched) / len(spec.skills), matched, concerns


def _title_component(job: JobPosting, spec: JobSearchQuery) -> Component:
    # Ties go to the more specific (longer) role, e.g. "security analyst" over "cybersecurity".
    term, score = max(((t, role_match(t, job.title)) for t in _search_terms(spec)), key=lambda ts: (ts[1], len(ts[0])))
    if score >= 0.99:
        return score, f"Title matches “{term}”", None
    if score >= 0.5:
        return score, f"Title partially matches “{term}”", None
    return score, None, "Title differs from the roles you asked for"


def _location_component(job: JobPosting, spec: JobSearchQuery) -> Component:
    location_text = "; ".join(job.locations)
    accepts_remote = spec.remote in ("remote_ok", "remote_only")
    if spec.remote == "remote_only":
        if job.remote == "remote":
            return 1.0, "Remote", None
        if job.remote is None:
            return UNKNOWN_SCORE, None, "Remote status unknown"
        return 0.0, None, f"Not remote ({job.remote})"
    if spec.remote == "onsite_only" and job.remote == "remote":
        return 0.2, None, "Remote role (you asked for on-site)"
    matched = next((loc for loc in spec.locations if location_matches(loc, location_text)), None)
    if matched:
        return 1.0, f"{matched}" + (f" · {job.remote}" if job.remote else ""), None
    if job.remote == "remote" and accepts_remote:
        if spec.locations and location_text:
            return 0.5, "Remote", f"Remote, but listed for {location_text}"
        return 1.0, "Remote", None
    if not location_text and job.remote is None:
        return UNKNOWN_SCORE, None, "Location unknown"
    if job.locations and all(loc.strip().lower() in _INDIA_COUNTRY_LABELS for loc in job.locations) \
            and is_india_location(spec.locations):
        return UNKNOWN_SCORE, None, "Listed for India (city not stated)"  # country-wide, not a known mismatch
    return (0.3 if not spec.locations else 0.0), None, f"Location: {location_text or job.remote}"


def _experience_component(job: JobPosting, spec: JobSearchQuery) -> Optional[Component]:
    if spec.min_experience_years is not None or spec.max_experience_years is not None:
        user_low, user_high = spec.min_experience_years or 0.0, spec.max_experience_years
    elif spec.experience_level:
        user_low, user_high = _LEVEL_YEARS[spec.experience_level]
    else:
        return None
    stated_years = job.experience_min_years is not None
    if stated_years:
        job_low, job_high = job.experience_min_years or 0.0, job.experience_max_years
    elif job.experience_level:
        job_low, job_high = _LEVEL_YEARS[job.experience_level]
    else:
        return UNKNOWN_SCORE, None, "Experience requirement not stated"

    described = _fmt_years(job_low, job_high) if stated_years else f"{job.experience_level}-level role"
    if user_high is not None and job_low > user_high:
        gap = job_low - user_high
        concern = f"Requires {job_low:g}+ years experience" if stated_years else f"{described.capitalize()}"
        return max(0.0, 1 - gap / _EXPERIENCE_GAP_TOLERANCE_YEARS), None, concern
    if job_high is not None and job_high < user_low:
        return 0.7, None, f"Pitched below your experience ({described})"
    return 1.0, described if stated_years else f"{(job.experience_level or '').capitalize()} level", None


def _freshness_component(job: JobPosting, spec: JobSearchQuery, now: datetime.datetime) -> Component:
    window = spec.posted_within_days or DEFAULT_FRESHNESS_WINDOW_DAYS
    if job.posted_at is None:
        return UNKNOWN_FRESHNESS_SCORE, None, "Posting date unknown"
    age = max(0.0, (now - job.posted_at).total_seconds() / 86_400)
    approx = "~" if job.posted_at_estimated else ""
    label = "today" if age < 1 else f"{approx}{int(age)} day{'s' if int(age) != 1 else ''} ago"
    if age <= window:
        return 1 - 0.5 * age / window, f"Posted {label}", None
    return max(0.0, 0.5 - 0.5 * (age - window) / window), None, f"Posted {label} (outside {window}-day window)"


def _employment_component(job: JobPosting, spec: JobSearchQuery) -> Component:
    if job.employment_type is None:
        return UNKNOWN_SCORE, None, "Employment type not stated"
    label = _EMPLOYMENT_LABELS[job.employment_type]
    if job.employment_type in spec.employment_types:
        return 1.0, label, None
    wanted = ", ".join(_EMPLOYMENT_LABELS[t] for t in spec.employment_types)
    return 0.0, None, f"{label} (you asked for {wanted})"


def _application_component(job: JobPosting) -> Component:
    if not job.application_url:
        return 0.0, None, "No application link found"
    score = _APPLICATION_BASE[job.application_type]
    if job.status == "active" and job.application_verified:
        return min(1.0, score + 0.15), "Application link verified", None
    if job.status == "possibly_closed":
        return score * 0.3, None, "Posting may be closed"
    return score, None, None


def score_job(job: JobPosting, spec: JobSearchQuery, now: datetime.datetime) -> None:
    """Compute match_score (0-100), per-component scores, reasons and concerns — in place."""
    components: Dict[str, float] = {}
    reasons: List[str] = []
    concerns: List[str] = []

    def record(name: str, component: Optional[Component]) -> None:
        if component is None:
            return
        score, reason, concern = component
        components[name] = score
        if reason:
            reasons.append(reason)
        if concern:
            concerns.append(concern)

    if spec.skills:
        skill_score, matched, skill_concerns = _skills_component(job, spec)
        components["skills"] = skill_score
        reasons.extend(matched)
        concerns.extend(skill_concerns)
    if _search_terms(spec):
        record("title", _title_component(job, spec))
    if spec.locations or spec.remote != "any":
        record("location", _location_component(job, spec))
    record("experience", _experience_component(job, spec))
    record("freshness", _freshness_component(job, spec, now))
    if spec.employment_types:
        record("employment", _employment_component(job, spec))
    record("application", _application_component(job))

    if spec.visa_sponsorship:
        if _NO_SPONSORSHIP.search(job.description):
            concerns.append("States no visa sponsorship")
        elif _SPONSORSHIP_OFFERED.search(job.description):
            reasons.append("Visa sponsorship mentioned")
        else:
            concerns.append("Visa sponsorship / work authorization unclear")
    if (spec.salary_min and job.salary_max and job.salary_currency
            and job.salary_currency == (spec.salary_currency or job.salary_currency) and job.salary_max < spec.salary_min):
        concerns.append("Salary below your minimum")
    if spec.include_companies and any(company_key(c) and company_key(c) in company_key(job.company) for c in spec.include_companies):
        reasons.append("Preferred company")

    total_weight = sum(WEIGHTS[name] for name in components)
    weighted = sum(WEIGHTS[name] * score for name, score in components.items())
    job.match_score = round(100 * weighted / total_weight) if total_weight else 0
    job.score_breakdown = {name: round(score, 2) for name, score in components.items()}
    job.match_reasons = reasons
    job.concerns = concerns


def _is_excluded(job: JobPosting, spec: JobSearchQuery, now: datetime.datetime) -> bool:
    if job.status == "closed":
        return True
    job_company = company_key(job.company)
    if job_company and any(company_key(c) and company_key(c) in job_company for c in spec.exclude_companies):
        return True
    if spec.posted_within_days and job.posted_at:
        return (now - job.posted_at).days > 2 * spec.posted_within_days
    return False


def _is_relevant(job: JobPosting) -> bool:
    """A job must be somewhere the user can work, and match the requested role or a requested skill.

    Location is a hard constraint only on a *known* mismatch; unknown locations score neutrally.
    """
    breakdown = job.score_breakdown
    if breakdown.get("location") == 0.0:
        return False
    has_title, has_skills = "title" in breakdown, "skills" in breakdown
    if not has_title and not has_skills:
        return True
    return (has_title and breakdown["title"] > 0) or (has_skills and breakdown["skills"] > 0)


def rank_jobs(jobs: List[JobPosting], spec: JobSearchQuery, now: datetime.datetime) -> Tuple[List[JobPosting], int]:
    """Score, filter and order jobs. Returns ``(qualified_jobs_best_first, rejected_count)``."""
    qualified: List[JobPosting] = []
    for job in jobs:
        if _is_excluded(job, spec, now):
            continue
        score_job(job, spec, now)
        if _is_relevant(job) and job.match_score >= QUALIFY_THRESHOLD:
            qualified.append(job)
    qualified.sort(key=lambda j: (
        -j.match_score,
        -APPLICATION_TYPE_RANK[j.application_type],
        -(j.posted_at.timestamp() if j.posted_at else 0.0),
    ))
    return qualified, len(jobs) - len(qualified)
