"""
job_search_plugin/query.py
─────────────────────────────────────────────────────────────────────────────
Query understanding and expansion.

  build_query()             natural language (+ optional filters, + optional LLM)
                            → validated ``JobSearchQuery``
  generate_search_queries() ``JobSearchQuery`` → bounded, de-duplicated search matrix
                            (role × location broad queries interleaved with
                            source-targeted ``site:`` queries)
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from backend.plugins.job_search_plugin.llm_assist import llm_parse_query
from backend.plugins.job_search_plugin.normalize import (
    KNOWN_LOCATIONS,
    canonical_location,
    canonical_skill,
    detect_level,
    find_employment_types,
    find_skills,
    is_india_location,
    parse_experience,
)
from backend.plugins.job_search_plugin.schema import JobSearchFilters, JobSearchQuery
from backend.plugins.job_search_plugin.urls import SOURCE_PROFILES, SourceProfile

MAX_SEARCH_QUERIES: int = 10
_MAX_ROLES_IN_MATRIX: int = 3
_MAX_PLACES_IN_MATRIX: int = 3
_MAX_SITE_QUERIES: int = 5
_MAX_ROLES: int = 5
_MAX_ROLE_CHARS: int = 40
_MAX_KEYWORDS: int = 4

# Order in which job sources get a dedicated site: query (regional boards are promoted first).
_SITE_PRIORITY: Tuple[str, ...] = (
    "LinkedIn", "Greenhouse", "Lever", "Ashby", "Wellfound", "Indeed", "Workday", "SmartRecruiters", "Workable",
)

# ─────────────────────────────────────────────────────────────────────────────
# Rule-based parsing
# ─────────────────────────────────────────────────────────────────────────────

_ROLE_NOUNS = (
    "engineer|analyst|developer|scientist|architect|administrator|consultant|specialist|manager|designer"
    "|tester|researcher|intern|associate|technician|officer|auditor|programmer"
)
_ROLE_WITH_NOUN = re.compile(rf"\b((?:[a-z][\w+#./-]*\s+){{0,3}}(?:{_ROLE_NOUNS})s?)\b", re.I)
_ROLE_BEFORE_JOB_WORD = re.compile(
    r"\b((?:[a-z][\w+#./-]*[\s,]+){0,4}?[a-z][\w+#./-]*)\s+"
    r"(?:jobs?|roles?|positions?|internships?|openings?|vacanc(?:y|ies)|opportunit(?:y|ies))\b",
    re.I,
)
_CONJUNCTION_SPLIT = re.compile(r"\s*(?:,|/|\band\b|\bor\b|&)\s*", re.I)
_FILLER_WORDS = frozenset({
    "find", "search", "searching", "looking", "look", "for", "me", "my", "some", "any", "all", "get", "show",
    "list", "and", "or", "the", "a", "an", "of", "to", "in", "junior", "jr", "senior", "sr", "entry", "level",
    "entry-level", "remote", "new", "latest", "recent", "top", "best", "paid", "unpaid", "fresher", "freshers",
    "part-time", "full-time", "part", "full", "time", "hybrid", "onsite", "on-site", "i", "want", "need",
    "prefer", "preferably", "also", "other", "related", "good", "great", "open", "available", "current",
    "internship", "internships", "intern", "interns", "job", "jobs", "role", "roles", "please", "suitable",
    "relevant", "similar", "like", "such", "as", "with", "requiring",
})

# A capitalised place of up to three words; each word needs 2+ letters so a following "I" is never swallowed.
_PLACE = r"(?:[Rr]emote\s+)?[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){0,2}"
_LOCATION_LEAD = re.compile(rf"\b(?:in|near|around|based\s+in|located\s+in|preferably|within|across)\s+({_PLACE})")
_LOCATION_CHAIN = re.compile(rf"^\s*(?:,|/|\bor\b|\band\b)\s*({_PLACE})")
_NOT_LOCATIONS = frozenset({"i", "the", "a", "an", "remote", "hybrid", "onsite", "linkedin", "indeed", "naukri"})

_REMOTE_ONLY = re.compile(r"remote\s+only|only\s+remote|fully\s+remote|100%\s+remote", re.I)
_ONSITE_ONLY = re.compile(r"\bno\s+remote\b|on[\s-]?site\s+only|only\s+on[\s-]?site|in[\s-]office\s+only", re.I)
_REMOTE_OK = re.compile(r"\bremote\b|work\s+from\s+home|\bwfh\b|\bhybrid\b", re.I)
_ENTRY_CUES = re.compile(r"\bfresher\b|new\s+grad(?:uate)?|recent\s+grad(?:uate)?|no\s+experience", re.I)

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10,
                 "fourteen": 14, "thirty": 30}
_FRESHNESS_N = re.compile(
    r"\b(?:last|past|within(?:\s+the)?(?:\s+(?:last|past))?)\s+"
    r"(\d{1,3}|one|two|three|four|five|six|seven|ten|fourteen|thirty)\s*(hour|day|week|month)s?\b",
    re.I,
)
_FRESHNESS_PHRASES: Tuple[Tuple[re.Pattern, int], ...] = (
    (re.compile(r"\btoday\b|\b(?:last|past)\s+24\s+hours\b", re.I), 1),
    (re.compile(r"\b(?:this|last|past)\s+week\b", re.I), 7),
    (re.compile(r"\bfortnight\b", re.I), 14),
    (re.compile(r"\b(?:this|last|past)\s+month\b", re.I), 30),
)
_UNIT_DAYS = {"hour": 1 / 24, "day": 1, "week": 7, "month": 30}

_SALARY = re.compile(
    r"(?:salary|stipend|pay|ctc|compensation)\D{0,20}?([₹$€£])?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|lpa|lakhs?)?\b\s*(inr|usd|eur|gbp)?",
    re.I,
)
_CURRENCY_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP"}
_VISA = re.compile(r"visa\s+sponsor|sponsorship|need\s+(?:a\s+)?visa", re.I)
_EDUCATION = re.compile(
    r"\b(?:bachelor'?s|master'?s|b\.?\s?tech|m\.?\s?tech|b\.?sc|m\.?sc|mba|ph\.?d|new\s+graduate|diploma)\b", re.I
)
_WORD = re.compile(r"[A-Za-z][\w+#.-]*")


def _clean_role(phrase: str) -> Optional[str]:
    """Keep the segment nearest the job word, strip filler words from both ends."""
    segment = _CONJUNCTION_SPLIT.split(phrase.strip())[-1]
    words = segment.split()
    while words and words[0].lower() in _FILLER_WORDS:
        words.pop(0)
    while words and words[-1].lower() in _FILLER_WORDS:
        words.pop()
    role = " ".join(words)
    if not role or len(role) > _MAX_ROLE_CHARS or canonical_location(role) in KNOWN_LOCATIONS:
        return None
    return role


def _extract_roles(text: str) -> List[str]:
    found: List[Tuple[int, str]] = []
    for pattern in (_ROLE_WITH_NOUN, _ROLE_BEFORE_JOB_WORD):
        for match in pattern.finditer(text):
            role = _clean_role(match.group(1))
            if role:
                found.append((match.start(), role))
    roles: Dict[str, str] = {}
    for _, role in sorted(found):
        roles.setdefault(role.lower(), role)
    # Drop roles that are a strict fragment of a longer one ("analyst" when "security analyst" exists).
    unique = list(roles.values())
    return [r for r in unique if not any(r.lower() != o.lower() and r.lower() in o.lower().split() for o in unique)]


def _extract_locations(text: str) -> Tuple[List[str], bool]:
    """Locations in order of appearance, plus whether one was phrased as 'remote <place>'."""
    found: List[Tuple[int, str]] = []
    remote_phrase = False

    def add(position: int, candidate: str) -> None:
        nonlocal remote_phrase
        place = candidate.strip(" .,;")
        if place.lower().startswith("remote"):
            remote_phrase = True
            place = place[6:].strip()
        if not place or place.lower() in _NOT_LOCATIONS or find_skills(place) or detect_level(place):
            return
        found.append((position, place))

    for match in _LOCATION_LEAD.finditer(text):
        add(match.start(1), match.group(1))
        tail = match.end()
        while (chained := _LOCATION_CHAIN.match(text[tail:])) is not None:
            add(tail + chained.start(1), chained.group(1))
            tail += chained.end()

    lowered = text.lower()
    for term in KNOWN_LOCATIONS:
        for match in re.finditer(rf"(?<![\w.]){re.escape(term)}(?![\w])", lowered):
            found.append((match.start(), text[match.start():match.end()]))

    locations: Dict[str, str] = {}
    for _, place in sorted(found):
        locations.setdefault(canonical_location(place), place.title() if place.islower() else place)
    return list(locations.values()), remote_phrase


def _extract_freshness(text: str) -> Optional[int]:
    match = _FRESHNESS_N.search(text)
    if match:
        raw_number = match.group(1).lower()
        number = int(raw_number) if raw_number.isdigit() else _NUMBER_WORDS[raw_number]
        return max(1, math.ceil(number * _UNIT_DAYS[match.group(2).lower()]))
    return next((days for pattern, days in _FRESHNESS_PHRASES if pattern.search(text)), None)


def _extract_salary(text: str) -> Tuple[Optional[float], Optional[str]]:
    match = _SALARY.search(text)
    if not match:
        return None, None
    symbol, number, unit, code = match.groups()
    value = float(number.replace(",", ""))
    unit = (unit or "").lower()
    if unit == "k":
        value *= 1_000
    elif unit in ("lpa", "lakh", "lakhs"):
        value *= 100_000
        code = code or "INR"
    currency = (code or "").upper() or _CURRENCY_SYMBOLS.get(symbol or "")
    return value, currency or None


def parse_rule_based(prompt: str) -> Dict[str, Any]:
    """Deterministically extract search fields from a natural-language request."""
    fields: Dict[str, Any] = {
        "roles": _extract_roles(prompt)[:_MAX_ROLES],
        "skills": find_skills(prompt),
        "employment_types": find_employment_types(prompt),
    }
    locations, remote_phrase = _extract_locations(prompt)
    fields["locations"] = locations

    if _REMOTE_ONLY.search(prompt):
        fields["remote"] = "remote_only"
    elif _ONSITE_ONLY.search(prompt):
        fields["remote"] = "onsite_only"
    elif remote_phrase or _REMOTE_OK.search(prompt):
        fields["remote"] = "remote_ok"

    experience = parse_experience(prompt, require_context=False)
    if experience:
        fields["min_experience_years"], fields["max_experience_years"] = experience
    level = detect_level(prompt) or ("entry" if _ENTRY_CUES.search(prompt) else None)
    if level:
        fields["experience_level"] = level

    fields["posted_within_days"] = _extract_freshness(prompt)
    fields["salary_min"], fields["salary_currency"] = _extract_salary(prompt)
    if _VISA.search(prompt):
        fields["visa_sponsorship"] = True
    education = _EDUCATION.search(prompt)
    if education:
        fields["education"] = education.group(0)

    if not fields["roles"] and not fields["skills"]:
        location_keys = {canonical_location(loc) for loc in locations}
        words = [w for w in _WORD.findall(prompt)
                 if w.lower() not in _FILLER_WORDS and canonical_location(w) not in location_keys
                 and not w.isdigit() and len(w) > 2]
        fields["keywords"] = words[:_MAX_KEYWORDS]
    return {k: v for k, v in fields.items() if v not in (None, [], "")}


# ─────────────────────────────────────────────────────────────────────────────
# Merging parsed fields, LLM fields and dashboard filters
# ─────────────────────────────────────────────────────────────────────────────

def _union(first: List[str], second: List[str], key=str.lower) -> List[str]:
    merged: Dict[str, str] = {}
    for item in first + second:
        merged.setdefault(key(item), item)
    return list(merged.values())


def _merge_llm(rule: Dict[str, Any], llm: Dict[str, Any]) -> Dict[str, Any]:
    """Union list fields (LLM roles first — it reads intent better); rule-based scalars win."""
    merged = dict(rule)
    merged["roles"] = _union(llm.get("roles", []), rule.get("roles", []))[:_MAX_ROLES]
    merged["skills"] = _union(rule.get("skills", []), [canonical_skill(s) for s in llm.get("skills", [])])
    merged["locations"] = _union(rule.get("locations", []), llm.get("locations", []), key=canonical_location)
    for key in ("include_companies", "exclude_companies"):
        if llm.get(key):
            merged[key] = llm[key]
    for key in ("remote", "experience_level", "min_experience_years", "max_experience_years",
                "posted_within_days", "industry", "education", "visa_sponsorship"):
        if merged.get(key) in (None, "any") and llm.get(key) is not None:
            merged[key] = llm[key]
    if merged.get("roles") or merged.get("skills"):
        merged.pop("keywords", None)
    return merged


def _apply_filters(fields: Dict[str, Any], filters: JobSearchFilters) -> Dict[str, Any]:
    """Explicit dashboard filters always override values parsed from the prompt."""
    merged = dict(fields)
    if filters.role:
        merged["roles"] = _union([filters.role], merged.get("roles", []))[:_MAX_ROLES]
    if filters.locations:
        merged["locations"] = filters.locations
    if filters.skills:
        merged["skills"] = _union([canonical_skill(s) for s in filters.skills], merged.get("skills", []))
    if filters.employment_type:
        merged["employment_types"] = [filters.employment_type]
    for key in ("remote", "min_experience_years", "max_experience_years", "posted_within_days",
                "salary_min", "salary_currency", "limit"):
        value = getattr(filters, key)
        if value is not None:
            merged[key] = value
    return merged


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error.get("loc", ()) if part != "__root__")
    message = error.get("msg", "invalid value").removeprefix("Value error, ")
    return f"{location}: {message}" if location else message


async def build_query(
    prompt: str, filters: Optional[Dict[str, Any]], llm_config: Optional[Dict[str, Any]]
) -> Tuple[JobSearchQuery, str]:
    """Build the validated search spec. Returns ``(spec, parser_label)``.

    Raises ``ValueError`` with a user-safe message when the request is unusable.
    """
    try:
        parsed_filters = JobSearchFilters.model_validate(filters or {})
    except ValidationError as exc:
        raise ValueError(f"Invalid filters — {_first_error(exc)}") from None

    text = " ".join((prompt or "").split())[:2_000] or (parsed_filters.role or "")
    if not text:
        raise ValueError("Describe the jobs you are looking for.")

    fields = parse_rule_based(text)
    parser = "rule-based parser"
    llm_fields = await llm_parse_query(text, llm_config)
    if llm_fields is not None:
        fields = _merge_llm(fields, llm_fields)
        parser = f"rule-based parser + {llm_config.get('provider')} ({llm_config.get('model')})" if llm_config else parser
    fields = _apply_filters(fields, parsed_filters)

    try:
        return JobSearchQuery(original=text, **fields), parser
    except ValidationError as exc:
        raise ValueError(_first_error(exc)) from None


# ─────────────────────────────────────────────────────────────────────────────
# Search matrix
# ─────────────────────────────────────────────────────────────────────────────

def _quote(term: str) -> str:
    return f'"{term}"' if " " in term.strip() else term.strip()


def _join(*parts: str) -> str:
    return " ".join(p.strip() for p in parts if p and p.strip())


def _qualifier(spec: JobSearchQuery) -> str:
    """The seniority / employment word that sharpens every query ('internship', 'entry level', ...)."""
    if "internship" in spec.employment_types or spec.experience_level == "internship":
        return "internship"
    if spec.experience_level == "entry":
        return "entry level"
    if spec.experience_level in ("senior", "lead"):
        return spec.experience_level
    if spec.employment_types and spec.employment_types[0] != "full_time":
        return spec.employment_types[0].replace("_", " ")
    return ""


def select_sources(spec: JobSearchQuery) -> List[SourceProfile]:
    """Sources worth a dedicated site: query for this search, best first."""
    in_india = is_india_location(spec.locations)
    internship = "internship" in spec.employment_types or spec.experience_level == "internship"
    preferred = {s.lower() for s in spec.preferred_sources}

    def eligible(profile: SourceProfile) -> bool:
        if not profile.site_query:
            return False
        if profile.regions and not ("india" in profile.regions and in_india):
            return False
        return internship or not profile.internship_focused

    def priority(profile: SourceProfile) -> Tuple[bool, bool, int]:
        order = _SITE_PRIORITY.index(profile.name) if profile.name in _SITE_PRIORITY else len(_SITE_PRIORITY)
        return profile.name.lower() not in preferred, not profile.regions, order

    return sorted((p for p in SOURCE_PROFILES if eligible(p)), key=priority)


def generate_search_queries(spec: JobSearchQuery) -> List[str]:
    """Bounded search matrix: broad role × place queries interleaved with site-targeted ones."""
    roles = spec.roles[:_MAX_ROLES_IN_MATRIX] or spec.keywords[:2] or [" ".join(spec.skills[:2])]
    qualifier = _qualifier(spec)
    job_word = "" if qualifier == "internship" else "jobs"
    places = spec.locations[:2]
    if spec.remote == "remote_only":
        place_terms = [f"remote {p}" for p in places] or ["remote"]
    else:
        place_terms = places + (["remote"] if spec.remote == "remote_ok" else [])
    place_terms = place_terms[:_MAX_PLACES_IN_MATRIX] or [""]

    broad = [_join(_quote(role), qualifier, job_word, place) for role in roles for place in place_terms]
    primary_role, primary_place = roles[0], place_terms[0]
    site = [
        _join(f"site:{profile.site_query}", _quote(primary_role), qualifier, primary_place)
        for profile in select_sources(spec)[:_MAX_SITE_QUERIES]
    ]
    extra_skills = [s for s in spec.skills if s.lower() not in primary_role.lower()][:2]
    skill_anchored = [_join(_quote(primary_role), qualifier, *extra_skills, primary_place)] if extra_skills else []

    interleaved: List[str] = []
    for i in range(max(len(broad), len(site))):
        interleaved.extend(q for q in (broad[i] if i < len(broad) else None, site[i] if i < len(site) else None) if q)
        if i == 0:
            interleaved.extend(skill_anchored)
    return list(dict.fromkeys(interleaved))[:MAX_SEARCH_QUERIES]
