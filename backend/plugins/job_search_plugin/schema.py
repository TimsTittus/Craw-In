"""
job_search_plugin/schema.py
─────────────────────────────────────────────────────────────────────────────
Typed contracts for the Job Search plugin:

  • JobSearchFilters — optional structured filters sent by the dashboard
  • JobSearchQuery   — the validated search specification the pipeline runs on
  • JobPosting       — one normalised, deduplicated job record
  • JobSearchReport  — the final result contract returned to the task layer

Unknown values are always ``None`` / empty — never guessed.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EmploymentType = Literal["full_time", "part_time", "contract", "internship", "temporary"]
ExperienceLevel = Literal["internship", "entry", "mid", "senior", "lead"]
RemotePreference = Literal["any", "remote_ok", "remote_only", "onsite_only"]
WorkMode = Literal["remote", "hybrid", "onsite"]
ApplicationType = Literal["direct_company", "ats", "job_board", "external", "unknown"]
SourceKind = Literal["job_board", "ats", "company", "web"]
JobStatus = Literal["active", "possibly_closed", "closed", "unknown"]

DEFAULT_RESULT_LIMIT: int = 25
MAX_RESULT_LIMIT: int = 50

_MAX_LIST_ITEMS: int = 10
_MAX_TERM_CHARS: int = 80

# Form entries meaning "nothing specified" ("Skills: Nil") must never become a hard criterion.
_PLACEHOLDER_TERMS = frozenset({
    "nil", "none", "null", "na", "n/a", "n.a", "n.a.", "no", "nothing", "not applicable", "any", "anything",
    "-", "--", ".", "nope",
})


def is_placeholder(term: str) -> bool:
    return term.strip().lower() in _PLACEHOLDER_TERMS


def _clean_terms(value: Any) -> List[str]:
    """Accept a list or comma-separated string; strip, de-duplicate (case-insensitive) and bound it."""
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)):
        raise ValueError("expected a list of strings or a comma-separated string")
    seen: set = set()
    cleaned: List[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        term = " ".join(item.split())[:_MAX_TERM_CHARS]
        if term and not is_placeholder(term) and term.lower() not in seen:
            seen.add(term.lower())
            cleaned.append(term)
    return cleaned[:_MAX_LIST_ITEMS]


class JobSearchFilters(BaseModel):
    """Optional structured filters from the dashboard. Each one overrides the parsed prompt."""

    model_config = ConfigDict(extra="ignore")

    role: Optional[str] = Field(None, max_length=_MAX_TERM_CHARS)
    locations: List[str] = Field(default_factory=list)
    remote: Optional[RemotePreference] = None
    skills: List[str] = Field(default_factory=list)
    employment_type: Optional[EmploymentType] = None
    min_experience_years: Optional[float] = Field(None, ge=0, le=50)
    max_experience_years: Optional[float] = Field(None, ge=0, le=50)
    posted_within_days: Optional[int] = Field(None, ge=1, le=365)
    salary_min: Optional[float] = Field(None, ge=0)
    salary_currency: Optional[str] = Field(None, max_length=8)
    limit: Optional[int] = Field(None, ge=1, le=MAX_RESULT_LIMIT)

    @field_validator("locations", "skills", mode="before")
    @classmethod
    def _terms(cls, value: Any) -> List[str]:
        return _clean_terms(value)

    @field_validator("role", "salary_currency", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and (not value.strip() or is_placeholder(value)):
            return None
        return value.strip() if isinstance(value, str) else value


class JobSearchQuery(BaseModel):
    """Validated, structured representation of a job search request."""

    original: str = Field(..., min_length=1, max_length=2_000)
    roles: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    locations: List[str] = Field(default_factory=list)
    remote: RemotePreference = "any"
    employment_types: List[EmploymentType] = Field(default_factory=list)
    experience_level: Optional[ExperienceLevel] = None
    min_experience_years: Optional[float] = Field(None, ge=0, le=50)
    max_experience_years: Optional[float] = Field(None, ge=0, le=50)
    salary_min: Optional[float] = Field(None, ge=0)
    salary_currency: Optional[str] = None
    include_companies: List[str] = Field(default_factory=list)
    exclude_companies: List[str] = Field(default_factory=list)
    industry: Optional[str] = None
    education: Optional[str] = None
    visa_sponsorship: Optional[bool] = None
    posted_within_days: Optional[int] = Field(None, ge=1, le=365)
    preferred_sources: List[str] = Field(default_factory=list)
    limit: int = Field(DEFAULT_RESULT_LIMIT, ge=1, le=MAX_RESULT_LIMIT)

    @field_validator(
        "roles", "keywords", "skills", "locations",
        "include_companies", "exclude_companies", "preferred_sources",
        mode="before",
    )
    @classmethod
    def _terms(cls, value: Any) -> List[str]:
        return _clean_terms(value)

    @field_validator("employment_types", mode="before")
    @classmethod
    def _unique_types(cls, value: Any) -> Any:
        return list(dict.fromkeys(value)) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _check_consistency(self) -> "JobSearchQuery":
        if not (self.roles or self.keywords or self.skills):
            raise ValueError("Could not identify a role, skill or keyword to search for. "
                             "Try e.g. 'security analyst internships in Bangalore'.")
        if (
            self.min_experience_years is not None
            and self.max_experience_years is not None
            and self.min_experience_years > self.max_experience_years
        ):
            raise ValueError("min_experience_years cannot exceed max_experience_years")
        return self


class JobSourceRef(BaseModel):
    """One place a job was seen. Merged duplicates keep every reference."""

    name: str
    url: str
    kind: SourceKind


class JobPosting(BaseModel):
    """A normalised job record. Fields that could not be verified stay ``None``."""

    job_id: str
    title: str
    company: Optional[str] = None
    locations: List[str] = Field(default_factory=list)
    remote: Optional[WorkMode] = None
    employment_type: Optional[EmploymentType] = None
    experience_level: Optional[ExperienceLevel] = None
    experience_min_years: Optional[float] = None
    experience_max_years: Optional[float] = None
    skills: List[str] = Field(default_factory=list)
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = None
    salary_period: Optional[str] = None
    description: str = ""
    requirements: List[str] = Field(default_factory=list)
    preferred_qualifications: List[str] = Field(default_factory=list)
    posted_at: Optional[datetime.datetime] = None
    posted_at_estimated: bool = False
    valid_through: Optional[datetime.datetime] = None
    source: str
    source_url: str
    sources: List[JobSourceRef] = Field(default_factory=list)
    application_url: Optional[str] = None
    application_type: ApplicationType = "unknown"
    application_verified: bool = False
    discovered_at: datetime.datetime
    last_verified_at: Optional[datetime.datetime] = None
    status: JobStatus = "unknown"
    extraction_method: Literal["json_ld", "heuristic", "llm_assisted"] = "heuristic"
    match_score: int = 0
    score_breakdown: Dict[str, float] = Field(default_factory=dict)
    match_reasons: List[str] = Field(default_factory=list)
    concerns: List[str] = Field(default_factory=list)
    raw: Dict[str, Any] = Field(default_factory=dict, description="Original source values, kept for debugging")


class SearchQueryStatus(BaseModel):
    query: str
    status: Literal["ok", "failed"]
    results: int = 0
    error: Optional[str] = None


_MAX_STAGE_ITEMS: int = 200


class StageItem(BaseModel):
    """One entry behind a summary counter, so the dashboard can show what a number is made of."""

    label: str
    url: Optional[str] = None
    detail: Optional[str] = None
    ok: bool = True  # False → shown for context but not counted (e.g. a page that could not be fetched)


class JobSearchStages(BaseModel):
    """The items behind each counter in ``JobSearchSummary``. Display only, and bounded."""

    discovered: List[StageItem] = Field(default_factory=list)
    unique: List[StageItem] = Field(default_factory=list)
    crawled: List[StageItem] = Field(default_factory=list)
    qualified: List[StageItem] = Field(default_factory=list)
    verified_applications: List[StageItem] = Field(default_factory=list)

    @field_validator("discovered", "unique", "crawled", "qualified", "verified_applications")
    @classmethod
    def _bound(cls, value: List[StageItem]) -> List[StageItem]:
        return value[:_MAX_STAGE_ITEMS]


class JobSearchSummary(BaseModel):
    queries_generated: int = 0
    searches_failed: int = 0
    discovered: int = 0
    unique: int = 0
    duplicates_removed: int = 0
    crawled: int = 0
    crawl_failures: int = 0
    extracted: int = 0
    rejected: int = 0
    qualified: int = 0
    returned: int = 0
    application_links_found: int = 0
    verified_applications: int = 0
    duration_seconds: float = 0.0


class JobSearchReport(BaseModel):
    query: Dict[str, Any]
    summary: JobSearchSummary = Field(default_factory=JobSearchSummary)
    stages: JobSearchStages = Field(default_factory=JobSearchStages)
    search_status: List[SearchQueryStatus] = Field(default_factory=list)
    jobs: List[JobPosting] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
