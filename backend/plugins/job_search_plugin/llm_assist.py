"""
job_search_plugin/llm_assist.py
─────────────────────────────────────────────────────────────────────────────
Optional LLM assistance through the shared ``backend.core.llm`` abstraction
(provider-agnostic: Ollama, OpenAI, DeepSeek, …).

Used for exactly two things:
  • interpreting ambiguous natural-language requests
  • reading title/company/location from pages that deterministic extraction missed

Every call is time-bounded; output is parsed as JSON, validated and grounded —
a string value is accepted only if it literally appears in the source text.
Any failure returns ``None`` and the pipeline continues deterministically.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from backend.core.llm import LLMConfig, get_llm_client

_QUERY_TIMEOUT_S: float = 20.0
_EXTRACT_TIMEOUT_S: float = 25.0
_EXTRACT_MAX_CHARS: int = 3_500
_MAX_VALUE_CHARS: int = 120

_REMOTE_VALUES = ("remote_only", "remote_ok", "onsite_only", "any")
_LEVEL_VALUES = ("internship", "entry", "mid", "senior", "lead")

# The LLM may only set a structured value when the request contains a cue for it.
_REMOTE_CUE = re.compile(r"remote|work\s+from\s+home|\bwfh\b|on[\s-]?site|in[\s-]office|hybrid", re.I)
_LEVEL_CUE = re.compile(r"intern|junior|entry|graduate|\bgrad\b|fresher|\bmid\b|senior|lead|principal|staff|manager|beginner", re.I)
_EXPERIENCE_CUE = re.compile(r"\d\s*\+?\s*(?:years?|yrs?)|fresher|new\s+grad|no\s+experience", re.I)
_FRESHNESS_CUE = re.compile(r"\b(?:days?|weeks?|months?|today|hours?|fortnight|recent(?:ly)?)\b", re.I)
_VISA_CUE = re.compile(r"visa|sponsor", re.I)

_QUERY_SYSTEM_PROMPT = """You convert a job-search request into search parameters.
Return ONLY a JSON object with these keys:
  "roles": job titles or role families the user wants, copied from the request (e.g. "security analyst"),
  "skills": skills or technologies the user mentions,
  "locations": cities, regions or countries the user mentions (without the word "remote"),
  "remote": "remote_only", "remote_ok", "onsite_only" or "any",
  "experience_level": "internship", "entry", "mid", "senior", "lead" or null,
  "min_experience_years": number or null,
  "max_experience_years": number or null,
  "posted_within_days": integer or null,
  "include_companies": companies the user wants to work for,
  "exclude_companies": companies the user wants to avoid,
  "industry": string or null,
  "education": string or null,
  "visa_sponsorship": true if the user needs visa sponsorship, otherwise null.
Copy text from the request; never invent values. Use [] or null when the request does not say."""

_EXTRACT_SYSTEM_PROMPT = """You read the beginning of a web page and decide whether it is a single job posting.
Return ONLY a JSON object with these keys:
  "is_job_posting": true or false,
  "title": the job title exactly as written on the page, or null,
  "company": the hiring company exactly as written on the page, or null,
  "location": the job location exactly as written on the page, or null.
Never guess. Use null when the page does not state a value."""


def _normalize_for_grounding(text: str) -> str:
    return " ".join(re.sub(r"[‐‑‒–—]", "-", text).lower().split())


def _grounded(value: Any, source: str) -> Optional[str]:
    """Return the value only if it is a short string that literally occurs in ``source``."""
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip(" .,;:")
    if not cleaned or len(cleaned) > _MAX_VALUE_CHARS:
        return None
    return cleaned if _normalize_for_grounding(cleaned) in _normalize_for_grounding(source) else None


def _str_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any, low: float, high: float) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if low <= value <= high else None


async def _generate_json(system_prompt: str, user_prompt: str, llm_config: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
    try:
        config = LLMConfig.model_validate({**llm_config, "temperature": 0.0})
        client = get_llm_client(config)
        raw = await asyncio.wait_for(client.generate(system_prompt, user_prompt, response_format="json"), timeout=timeout)
        data = json.loads(raw)
    except Exception as exc:  # provider down, bad key, timeout, invalid JSON — all non-fatal
        print(f"[JobSearchPlugin] LLM assistance unavailable ({type(exc).__name__}); continuing rule-based", flush=True)
        return None
    return data if isinstance(data, dict) else None


async def llm_parse_query(prompt: str, llm_config: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Grounded structured fields for a job-search request, or None when no LLM is usable."""
    if not llm_config:
        return None
    data = await _generate_json(_QUERY_SYSTEM_PROMPT, prompt, llm_config, _QUERY_TIMEOUT_S)
    if data is None:
        return None

    fields: Dict[str, Any] = {}
    for key in ("roles", "skills", "locations", "include_companies", "exclude_companies"):
        fields[key] = [v for v in (_grounded(item, prompt) for item in _str_list(data.get(key))) if v]
    for key in ("industry", "education"):
        value = _grounded(data.get(key), prompt)
        if value:
            fields[key] = value
    if data.get("remote") in _REMOTE_VALUES and _REMOTE_CUE.search(prompt):
        fields["remote"] = data["remote"]
    if data.get("experience_level") in _LEVEL_VALUES and _LEVEL_CUE.search(prompt):
        fields["experience_level"] = data["experience_level"]
    if _EXPERIENCE_CUE.search(prompt):
        for key in ("min_experience_years", "max_experience_years"):
            value = _number(data.get(key), 0, 50)
            if value is not None:
                fields[key] = value
    if _FRESHNESS_CUE.search(prompt):
        days = _number(data.get("posted_within_days"), 1, 365)
        if days is not None:
            fields["posted_within_days"] = int(days)
    if data.get("visa_sponsorship") is True and _VISA_CUE.search(prompt):
        fields["visa_sponsorship"] = True
    return fields


async def llm_extract_job_fields(page_text: str, llm_config: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """Title/company/location for a page deterministic extraction could not read, grounded in the page."""
    if not llm_config or not page_text:
        return None
    excerpt = page_text[:_EXTRACT_MAX_CHARS]
    data = await _generate_json(_EXTRACT_SYSTEM_PROMPT, excerpt, llm_config, _EXTRACT_TIMEOUT_S)
    if not data or data.get("is_job_posting") is not True:
        return None
    fields = {k: v for k in ("title", "company", "location") if (v := _grounded(data.get(k), excerpt))}
    return fields if "title" in fields else None
