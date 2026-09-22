import asyncio
import datetime
import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.plugins import load_plugins
from backend.plugins.base import PluginManifest
from backend.plugins.job_search_plugin import llm_assist, urls
from backend.plugins.job_search_plugin.extract import harvest_posting_links, parse_job_page
from backend.plugins.job_search_plugin.match import merge_duplicates, rank_jobs
from backend.plugins.job_search_plugin.plugin import JobSearchPlugin
from backend.plugins.job_search_plugin.query import MAX_SEARCH_QUERIES, build_query, generate_search_queries
from backend.plugins.job_search_plugin.schema import JobSearchQuery

NOW = datetime.datetime.now(datetime.timezone.utc)
PUBLIC_IP = "93.184.215.14"
EXAMPLE_PROMPT = (
    "Find cybersecurity internships in Bangalore or remote India. I have 0–1 years of experience. "
    "Look for SOC, SIEM, Python, Linux, network security and security analyst roles. "
    "Prefer jobs posted within the last 14 days."
)
SECURITY_DESCRIPTION = (
    "<p>Join our Security Operations Center (SOC) team as an intern.</p>"
    "<h3>Requirements</h3><ul><li>0-1 years of experience in security monitoring</li>"
    "<li>Hands-on Python and Linux skills</li><li>Familiarity with SIEM tools such as Splunk</li>"
    "<li>Basic network security knowledge</li></ul>"
    "<h3>Nice to have</h3><ul><li>Security+ certification</li></ul>"
    + "<p>You will triage alerts, investigate incidents and document findings for the SOC team every day.</p>" * 6
)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Resolve every hostname to a public IP so tests never touch real DNS."""
    urls._DNS_CACHE.clear()
    monkeypatch.setattr(urls, "_resolve_host", AsyncMock(return_value=(PUBLIC_IP,)))


def _manifest() -> PluginManifest:
    return PluginManifest(name="job_search", description="test", version="1.0.0")


def _job_html(
    title: str,
    company: str,
    locality: str,
    *,
    country: str = "India",
    posted_days_ago: Optional[int] = 2,
    description: str = SECURITY_DESCRIPTION,
    employment: str = "INTERN",
    links: str = "",
    valid_through: Optional[str] = None,
    experience_months: Optional[int] = None,
    salary: Optional[Dict[str, Any]] = None,
) -> str:
    node: Dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": title,
        "hiringOrganization": {"@type": "Organization", "name": company},
        "jobLocation": {"@type": "Place", "address": {"addressLocality": locality, "addressCountry": country}},
        "employmentType": employment,
        "description": description,
    }
    if posted_days_ago is not None:
        node["datePosted"] = (NOW - datetime.timedelta(days=posted_days_ago)).date().isoformat()
    if valid_through:
        node["validThrough"] = valid_through
    if experience_months is not None:
        node["experienceRequirements"] = {"@type": "OccupationalExperienceRequirements", "monthsOfExperience": experience_months}
    if salary:
        node["baseSalary"] = salary
    return (
        f"<html><head><title>{title}</title><script type=\"application/ld+json\">{json.dumps(node)}</script></head>"
        f"<body><h1>{title}</h1>{links}</body></html>"
    )


def _page(html: str, url: str, content: str = "", source: str = "trafilatura") -> Dict[str, Any]:
    return {"url": url, "status": 200, "content": content or "Job description text " * 20,
            "source": source, "html": html, "final_url": url}


def _parse(url: str, html: str, content: str = "") -> Any:
    return parse_job_page(url=url, final_url=url, html=html, markdown=content or "Job description " * 30,
                          crawl_tier="trafilatura", now=NOW)


# ─────────────────────────────────────────────────────────────────────────────
# Plugin discovery
# ─────────────────────────────────────────────────────────────────────────────

def test_plugin_is_discovered_by_loader():
    plugins = load_plugins("backend/plugins")
    assert "error" not in plugins["job_search"]
    assert plugins["job_search"]["instance"].__class__.__name__ == "JobSearchPlugin"
    # Existing plugins still load alongside it
    assert "instance" in plugins["search_api"] and "instance" in plugins["person_recon"]


# ─────────────────────────────────────────────────────────────────────────────
# Query parsing & expansion
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parse_simple_request():
    spec, parser = await build_query("Cybersecurity internships in Bangalore", None, None)
    assert [r.lower() for r in spec.roles] == ["cybersecurity"]
    assert spec.employment_types == ["internship"]
    assert spec.locations == ["Bangalore"]
    assert parser == "rule-based parser"


@pytest.mark.asyncio
async def test_parse_full_natural_language_request():
    spec, _ = await build_query(EXAMPLE_PROMPT, None, None)
    assert {"cybersecurity", "security analyst"} <= {r.lower() for r in spec.roles}
    assert {"SOC", "SIEM", "Python", "Linux", "Network Security"} <= set(spec.skills)
    assert spec.locations == ["Bangalore", "India"]
    assert spec.remote == "remote_ok"
    assert spec.employment_types == ["internship"]
    assert (spec.min_experience_years, spec.max_experience_years) == (0, 1)
    assert spec.posted_within_days == 14


@pytest.mark.asyncio
async def test_parse_graduate_request_in_ireland():
    spec, _ = await build_query(
        "Find junior cybersecurity jobs in Ireland, preferably Dublin or remote, requiring Python, Linux and SIEM, "
        "suitable for a new graduate.", None, None)
    assert spec.locations == ["Ireland", "Dublin"]
    assert spec.remote == "remote_ok"
    assert spec.experience_level == "entry"
    assert {"Python", "Linux", "SIEM"} <= set(spec.skills)


@pytest.mark.asyncio
async def test_unsearchable_request_is_rejected():
    with pytest.raises(ValueError):
        await build_query("!!!", None, None)
    with pytest.raises(ValueError, match="limit"):
        await build_query("python developer jobs", {"limit": 5000}, None)


@pytest.mark.asyncio
async def test_structured_filters_override_prompt():
    spec, _ = await build_query("security analyst internships in Bangalore",
                                {"locations": "Pune, Mumbai", "employment_type": "full_time", "limit": 5,
                                 "skills": ["splunk"]}, None)
    assert spec.locations == ["Pune", "Mumbai"]
    assert spec.employment_types == ["full_time"]
    assert spec.limit == 5
    assert spec.skills[0] == "Splunk"



@pytest.mark.asyncio
async def test_placeholder_filter_values_are_ignored():
    spec, _ = await build_query("", {"role": "AI Researcher", "skills": "Nil", "locations": "Bangalore, none",
                                     "employment_type": "internship"}, None)
    assert spec.skills == [] and spec.locations == ["Bangalore"]
    assert not any("Nil" in q for q in generate_search_queries(spec))

@pytest.mark.asyncio
async def test_llm_values_must_be_grounded_in_the_prompt(monkeypatch):
    monkeypatch.setattr(llm_assist, "_generate_json", AsyncMock(return_value={
        "roles": ["security analyst", "astronaut"],
        "locations": ["Mars", "Pune"],
        "remote": "remote_only",          # no remote cue in the prompt → ignored
        "posted_within_days": 3,          # no freshness cue → ignored
        "exclude_companies": ["Globex"],  # not in the prompt → dropped
    }))
    spec, parser = await build_query("security analyst openings in Pune", None,
                                     {"provider": "local_ollama", "model": "auto"})
    assert "astronaut" not in [r.lower() for r in spec.roles]
    assert "Mars" not in spec.locations
    assert spec.remote == "any"
    assert spec.posted_within_days is None
    assert spec.exclude_companies == []
    assert "local_ollama" in parser


@pytest.mark.asyncio
async def test_llm_failure_degrades_to_rule_based():
    # An unreachable provider must not break parsing (api_key missing → config invalid).
    spec, parser = await build_query("python developer jobs in Berlin", None,
                                     {"provider": "cloud_openai", "model": "gpt-4o"})
    assert spec.roles == ["python developer"]
    assert parser == "rule-based parser"


@pytest.mark.asyncio
async def test_query_matrix_is_diverse_and_bounded():
    spec, _ = await build_query(EXAMPLE_PROMPT, None, None)
    queries = generate_search_queries(spec)
    assert 6 <= len(queries) <= MAX_SEARCH_QUERIES
    assert len(set(queries)) == len(queries)
    assert any(q.startswith("site:") for q in queries)
    assert any("naukri.com" in q or "internshala.com" in q for q in queries)  # India-specific sources
    assert any('"security analyst"' in q for q in queries)
    assert any("remote" in q for q in queries)


def test_query_matrix_skips_regional_sources_outside_region():
    spec = JobSearchQuery(original="x", roles=["data engineer"], locations=["Berlin"])
    queries = generate_search_queries(spec)
    assert not any("naukri" in q or "internshala" in q for q in queries)


# ─────────────────────────────────────────────────────────────────────────────
# URLs & SSRF
# ─────────────────────────────────────────────────────────────────────────────

def test_url_canonicalisation_and_platform_ids():
    assert urls.canonicalize_url("https://WWW.Example.com/jobs/123/?utm_source=x&gh_src=y&id=7#apply") == \
        "https://www.example.com/jobs/123?id=7"
    assert urls.canonicalize_url("javascript:alert(1)") is None
    assert urls.canonicalize_url("https://user:pw@example.com/") is None
    a = urls.dedup_key("https://www.linkedin.com/jobs/view/security-analyst-at-acme-3900000001?trk=public")
    b = urls.dedup_key("https://in.linkedin.com/jobs/view/3900000001/")
    assert a == b == "linkedin:3900000001"
    assert urls.dedup_key("https://in.indeed.com/viewjob?jk=abcdef12345&from=serp") == "indeed:abcdef12345"
    assert urls.classify_url("https://www.linkedin.com/jobs/search?keywords=soc") == "listing"
    assert urls.unwrap_redirect("https://www.linkedin.com/jobs/view/externalApply/1?url=https%3A%2F%2Facme.com%2Fjobs%2F9") \
        == "https://acme.com/jobs/9"


@pytest.mark.asyncio
async def test_ssrf_guard_blocks_internal_targets(monkeypatch):
    for url in ("http://localhost/", "http://127.0.0.1/admin", "http://10.0.0.5/", "http://169.254.169.254/latest/meta-data",
                "http://[::1]/", "file:///etc/passwd", "ftp://example.com/", "http://example.com:8080/",
                "http://service.internal/", "http://intranet/"):
        assert not await urls.is_public_url(url), url
    assert await urls.is_public_url(f"https://{PUBLIC_IP}/jobs/1")

    monkeypatch.setattr(urls, "_resolve_host", AsyncMock(return_value=("192.168.1.10",)))
    assert not await urls.is_public_url("https://rebound.example.org/")  # public name, private address


@pytest.mark.asyncio
async def test_ssrf_guard_blocks_redirect_to_internal_address():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == PUBLIC_IP:
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})
        return httpx.Response(200, text="secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True,
                                 event_hooks={"request": [urls.guard_request]}) as client:
        with pytest.raises(urls.BlockedURLError):
            await client.get(f"https://{PUBLIC_IP}/job")


# ─────────────────────────────────────────────────────────────────────────────
# Extraction & application links
# ─────────────────────────────────────────────────────────────────────────────

def test_json_ld_extraction_prefers_direct_company_application():
    links = (
        '<a href="https://www.linkedin.com/signup">Sign in to apply</a>'
        '<a href="https://boards.greenhouse.io/acme/jobs/7770001">Apply via Greenhouse</a>'
        '<a href="https://careers.acme.com/jobs/7770001">Apply on company website</a>'
    )
    html = _job_html("Security Analyst Intern", "Acme", "Bengaluru", links=links, experience_months=12,
                     salary={"@type": "MonetaryAmount", "currency": "inr",
                             "value": {"@type": "QuantitativeValue", "minValue": 15000, "maxValue": 25000, "unitText": "MONTH"}})
    job = _parse("https://www.linkedin.com/jobs/view/3900000001", html)
    assert job is not None and job.extraction_method == "json_ld"
    assert (job.title, job.company, job.locations) == ("Security Analyst Intern", "Acme", ["Bengaluru, India"])
    assert job.employment_type == "internship" and job.experience_level == "internship"
    assert job.experience_min_years == 1.0
    assert (job.salary_min, job.salary_max, job.salary_currency, job.salary_period) == (15000, 25000, "INR", "month")
    assert job.posted_at is not None and not job.posted_at_estimated
    assert {"SOC", "SIEM", "Python", "Linux", "Network Security"} <= set(job.skills)
    assert job.requirements and job.preferred_qualifications == ["Security+ certification"]
    assert job.source == "LinkedIn" and job.source_url == "https://www.linkedin.com/jobs/view/3900000001"
    assert job.application_url == "https://careers.acme.com/jobs/7770001"
    assert job.application_type == "direct_company"
    assert job.application_verified is False and job.last_verified_at is None


def test_ats_link_used_when_no_company_link():
    links = '<a href="https://www.linkedin.com/jobs/view/externalApply/1?url=https%3A%2F%2Fjobs.lever.co%2Facme%2F0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b">Apply</a>'
    job = _parse("https://www.linkedin.com/jobs/view/3900000002", _job_html("SOC Intern", "Acme", "Pune", links=links))
    assert job.application_url == "https://jobs.lever.co/acme/0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
    assert job.application_type == "ats"


def test_missing_fields_stay_unknown():
    html = ("<html><head><meta property='og:title' content='SOC Analyst'>"
            "<meta property='og:site_name' content='Initech'></head><body><h1>SOC Analyst</h1></body></html>")
    text = "Location: Bangalore\n\nAbout the role\nYou will monitor alerts in our SOC using Splunk.\n" + "Responsibilities include triage. " * 10
    job = _parse("https://careers.initech.com/jobs/4321", html, content=text)
    assert job is not None and job.extraction_method == "heuristic"
    assert job.company == "Initech" and job.locations == ["Bangalore"]
    assert job.posted_at is None and job.salary_min is None and job.experience_min_years is None
    assert job.employment_type is None and job.experience_level is None and job.remote is None
    assert job.application_url == job.source_url and job.application_type == "direct_company"

    spec = JobSearchQuery(original="x", roles=["SOC analyst"], skills=["Splunk"], locations=["Bangalore"],
                          min_experience_years=0, max_experience_years=1, employment_types=["internship"])
    ranked, _ = rank_jobs([job], spec, NOW)
    assert ranked, "a job with missing optional fields must still be returned"
    assert {"Posting date unknown", "Experience requirement not stated", "Employment type not stated"} <= set(ranked[0].concerns)
    assert ranked[0].score_breakdown["experience"] == 0.5  # unknown is neutral, never a fabricated match


def test_listing_pages_are_not_jobs_but_yield_posting_links():
    html = ('<a href="/jobs/view/security-intern-at-acme-3900000003?trk=x">Security Intern</a>'
            '<a href="/jobs/view/3900000003">dup</a><a href="/jobs/search?keywords=x">more</a>')
    url = "https://www.linkedin.com/jobs/search?keywords=soc"
    assert parse_job_page(url=url, final_url=url, html=html, markdown="list " * 100, crawl_tier="trafilatura", now=NOW) is None
    assert harvest_posting_links(html, url, 5) == ["https://www.linkedin.com/jobs/view/security-intern-at-acme-3900000003"]


def test_marketing_and_roundup_pages_are_not_jobs():
    text = "Apply now for our cyber security internship program. Experience with SOC tools. " * 10
    for title in ("Best Cyber Security Internship In Bangalore (2025)", "Cyber Security Internships", "Cyber and IT Interns"):
        html = f"<html><head><meta property='og:title' content='{title}'></head><body><a href='/enroll'>Apply now</a></body></html>"
        assert _parse("https://training.example.com/cyber-security-internship-in-bangalore", html, content=text) is None, title
    # Singular title, but no posting-shaped URL and no concrete job field → still not a posting.
    html = "<html><head><meta property='og:title' content='Cyber Security Internship'></head><body><a href='/enroll'>Apply now</a></body></html>"
    assert _parse("https://training.example.com/cyber-security-internship", html, content=text) is None
    assert _parse("https://training.example.com/cyber-security-internship", html, content="Location: Pune\n" + text) is not None


def test_platform_posting_urls_drop_navigation_params():
    assert urls.canonicalize_url("https://in.linkedin.com/jobs/view/analyst-at-x-4461603260?pageNum=0&position=5") == \
        "https://in.linkedin.com/jobs/view/analyst-at-x-4461603260"
    # Indeed's job id lives in the query, so the query is kept.
    assert urls.canonicalize_url("https://in.indeed.com/viewjob?jk=abc123def456") == "https://in.indeed.com/viewjob?jk=abc123def456"


def test_known_location_mismatch_is_excluded():
    abroad = _parse("https://www.linkedin.com/jobs/view/3900000031",
                    _job_html("Security Analyst Intern", "Globex", "Mountain View", country="United States"))
    local = _parse("https://www.linkedin.com/jobs/view/3900000032", _job_html("Security Analyst Intern", "Acme", "Bengaluru"))
    spec = JobSearchQuery(original="x", roles=["security analyst"], locations=["Bangalore", "India"], remote="remote_ok")
    ranked, rejected = rank_jobs([abroad, local], spec, NOW)
    assert [j.company for j in ranked] == ["Acme"] and rejected == 1



def test_country_only_location_and_intern_title_are_not_rejected():
    job = _parse("https://www.linkedin.com/jobs/view/3900000033",
                 _job_html("Security Analyst Intern", "Acme", "", country="IN", employment="FULL_TIME"))
    job.locations = ["IN"]
    assert job.employment_type == "internship"
    spec = JobSearchQuery(original="x", roles=["security analyst"], locations=["Bangalore"], employment_types=["internship"])
    ranked, rejected = rank_jobs([job], spec, NOW)
    assert len(ranked) == 1 and rejected == 0

def test_expired_posting_is_closed_and_excluded():
    job = _parse("https://www.linkedin.com/jobs/view/3900000004",
                 _job_html("Security Analyst Intern", "Acme", "Pune", valid_through="2020-01-01"))
    assert job.status == "closed"
    spec = JobSearchQuery(original="x", roles=["security analyst"])
    assert rank_jobs([job], spec, NOW) == ([], 1)


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication & ranking
# ─────────────────────────────────────────────────────────────────────────────

def test_same_job_across_sources_is_merged():
    apply = '<a href="https://boards.greenhouse.io/acme/jobs/7770001">Apply now</a>'
    linkedin = _parse("https://www.linkedin.com/jobs/view/3900000001", _job_html("Security Analyst Intern", "Acme", "Bengaluru", links=apply))
    indeed = _parse("https://in.indeed.com/viewjob?jk=abcdef1234567890", _job_html("Security Analyst Intern", "Acme Inc.", "Bangalore"))
    greenhouse = _parse("https://boards.greenhouse.io/acme/jobs/7770001", _job_html("Security Analyst Intern", "ACME", "Bengaluru, Karnataka"))
    other_city = _parse("https://www.linkedin.com/jobs/view/3900000009", _job_html("Security Analyst Intern", "Acme", "Pune"))
    other_company = _parse("https://www.linkedin.com/jobs/view/3900000010", _job_html("Security Analyst Intern", "Globex", "Bengaluru"))

    merged, removed = merge_duplicates([indeed, linkedin, other_city, greenhouse, other_company])
    assert removed == 2 and len(merged) == 3
    acme_blr = next(j for j in merged if len(j.sources) > 1)
    assert {s.name for s in acme_blr.sources} == {"LinkedIn", "Indeed", "Greenhouse"}
    assert acme_blr.application_url == "https://boards.greenhouse.io/acme/jobs/7770001"
    assert acme_blr.application_type == "ats"


def test_similar_titles_at_different_companies_are_not_merged_by_description():
    a = _parse("https://www.linkedin.com/jobs/view/3900000011", _job_html("Security Analyst", "Acme", "Pune"))
    b = _parse("https://www.linkedin.com/jobs/view/3900000012", _job_html("Security Analyst", "Globex", "Pune"))
    assert len(merge_duplicates([a, b])[0]) == 2


def test_stronger_matches_rank_higher():
    strong = _parse("https://www.linkedin.com/jobs/view/3900000021", _job_html("Security Analyst Intern", "Acme", "Bengaluru"))
    weak_description = "<p>Lead our cloud security architecture across AWS. 8+ years of experience required.</p>" * 5
    weak = _parse("https://www.linkedin.com/jobs/view/3900000022",
                  _job_html("Senior Security Architect", "Globex", "Mumbai", posted_days_ago=20,
                            employment="FULL_TIME", description=weak_description))
    spec = JobSearchQuery(original="x", roles=["security analyst"], skills=["SIEM", "Python", "Linux"],
                          locations=["Bangalore"], employment_types=["internship"],
                          min_experience_years=0, max_experience_years=1, posted_within_days=14)
    ranked, _ = rank_jobs([weak, strong], spec, NOW)
    assert ranked[0].company == "Acme"
    assert ranked[0].match_score >= 80
    assert {"SIEM", "Python", "Linux"} <= set(ranked[0].match_reasons)
    weak_scored = next((j for j in ranked if j.company == "Globex"), weak)
    assert weak_scored.match_score < ranked[0].match_score
    assert any("8+ years" in c for c in weak_scored.concerns)
    assert any("outside 14-day window" in c for c in weak_scored.concerns)


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end plugin execution (search → crawl → extract → dedup → rank → verify)
# ─────────────────────────────────────────────────────────────────────────────

LINKEDIN_URL = "https://www.linkedin.com/jobs/view/security-analyst-intern-at-acme-3900000001"
INDEED_URL = "https://in.indeed.com/viewjob?jk=abcdef1234567890"
GLOBEX_URL = "https://boards.greenhouse.io/globex/jobs/5550001"
INITECH_URL = "https://careers.initech.com/jobs/4321"
ACME_APPLY_URL = "https://boards.greenhouse.io/acme/jobs/7770001"

SEARCH_RESULTS = [
    {"title": "Acme hiring Security Analyst Intern in Bengaluru | LinkedIn", "url": LINKEDIN_URL + "?trk=public_jobs",
     "description": "SOC SIEM Python internship"},
    {"title": "Security Analyst Intern - Acme - Bengaluru", "url": INDEED_URL + "&from=serp", "description": "Internship"},
    {"title": "Senior Security Architect", "url": GLOBEX_URL, "description": "Security architecture"},
    {"title": "Top 10 cybersecurity internship tips", "url": "https://www.youtube.com/watch?v=abc"},
    {"title": "SOC Analyst Intern | Initech Careers", "url": INITECH_URL, "description": "SOC internship Bangalore"},
]


class FakeSearch:
    """Search provider where some queries fail, mirroring search_api's error contract."""

    def __init__(self) -> None:
        self.queries: List[str] = []

    async def execute(self, query: str, context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        self.queries.append(query)
        if "naukri.com" in query:
            raise RuntimeError("provider exploded")
        if "internshala.com" in query:
            return [{"error": "DuckDuckGo search failed with status 202"}]
        return [dict(r) for r in SEARCH_RESULTS]


def _fake_pages() -> Dict[str, Dict[str, Any]]:
    apply_link = f'<a href="https://www.linkedin.com/jobs/view/externalApply/1?url={ACME_APPLY_URL}">Apply on company website</a>'
    initech_html = ("<html><head><meta property='og:title' content='SOC Analyst Intern | Initech Careers'>"
                    "<meta property='og:site_name' content='Initech'></head><body></body></html>")
    initech_text = ("Location: Bangalore\n\nAbout the role\nMonitor SIEM alerts with Python and Linux.\n"
                    + "Responsibilities include incident triage. " * 10)
    globex = _job_html("Senior Security Architect", "Globex", "Mumbai", employment="FULL_TIME",
                       description="<p>Own security architecture. 8+ years of experience required.</p>" * 5)
    return {
        LINKEDIN_URL: _page(_job_html("Security Analyst Intern", "Acme", "Bengaluru", links=apply_link), LINKEDIN_URL),
        INDEED_URL: _page(_job_html("Security Analyst Intern", "Acme Inc.", "Bangalore"), INDEED_URL),
        GLOBEX_URL: _page(globex, GLOBEX_URL),
        INITECH_URL: _page(initech_html, INITECH_URL, content=initech_text),
    }


@pytest.mark.asyncio
async def test_end_to_end_search_with_partial_source_failures(monkeypatch):
    plugin = JobSearchPlugin(_manifest())
    pages = {urls.dedup_key(u): page for u, page in _fake_pages().items()}
    crawled: List[str] = []

    async def fake_process_url(url: str) -> Dict[str, Any]:
        crawled.append(url)
        return pages.get(urls.dedup_key(url), {"url": url, "content": "", "status": 404, "source": "none"})

    monkeypatch.setattr(plugin._crawler, "process_url", fake_process_url)
    monkeypatch.setattr(plugin, "_http_check", AsyncMock(return_value=("active", True)))

    events: List[tuple] = []

    async def emit(event: str, step: str, status: str, details: str) -> None:
        events.append((event, step, status, details))

    search = FakeSearch()
    report = (await plugin.execute(EXAMPLE_PROMPT, {"search": search, "emit": emit}))[0]

    assert report["error"] is None
    assert len(search.queries) == report["summary"]["queries_generated"] <= MAX_SEARCH_QUERIES
    failed = [s for s in report["search_status"] if s["status"] == "failed"]
    assert len(failed) == 2 and report["summary"]["searches_failed"] == 2
    assert any("search queries failed" in w for w in report["warnings"])

    assert not any("youtube" in u for u in crawled)  # non-job domain filtered before crawling
    assert len(crawled) == len(set(crawled))        # each unique URL crawled once

    jobs = report["jobs"]
    top = jobs[0]
    assert (top["title"], top["company"]) == ("Security Analyst Intern", "Acme")
    assert {s["name"] for s in top["sources"]} == {"LinkedIn", "Indeed"}
    assert top["application_url"] == ACME_APPLY_URL and top["application_type"] == "ats"
    assert top["status"] == "active" and top["application_verified"] is True
    assert top["last_verified_at"] is not None
    assert top["match_score"] >= 80 and "SIEM" in top["match_reasons"]
    assert report["summary"]["duplicates_removed"] == 1

    initech = next(j for j in jobs if j["company"] == "Initech")
    assert initech["posted_at"] is None and initech["salary_min"] is None
    assert all(j["company"] != "Globex" or j["match_score"] < top["match_score"] for j in jobs)
    assert [j["match_score"] for j in jobs] == sorted((j["match_score"] for j in jobs), reverse=True)

    # Every summary counter is backed by the list of items the dashboard expands under it.
    stages, summary = report["stages"], report["summary"]
    assert len(stages["discovered"]) == summary["discovered"]
    assert len(stages["unique"]) == summary["unique"]
    assert len(stages["qualified"]) == summary["qualified"]
    assert len(stages["verified_applications"]) == summary["verified_applications"]
    fetched = [i for i in stages["crawled"] if i["ok"]]
    assert len(fetched) == summary["crawled"]
    assert len(stages["crawled"]) - len(fetched) == summary["crawl_failures"]
    assert all(i["ok"] for i in stages["crawled"][:len(fetched)])  # fetched pages listed first
    assert all(item["label"] and str(item["url"]).startswith("http")
               for group in stages.values() for item in group)
    assert "seen" in next(i["detail"] for i in stages["unique"] if "linkedin" in i["url"])
    assert all("job extracted" in i["detail"] for i in fetched if i["label"] in (top["title"], "Senior Security Architect"))

    emitted = [e[0] for e in events]
    for expected in ("job_search_started", "query_parsed", "queries_generated", "search_started",
                     "search_source_completed", "candidates_discovered", "deduplication_completed",
                     "ranking_started", "crawl_started", "job_extracted", "application_link_found",
                     "verification_completed", "job_search_completed"):
        assert expected in emitted, expected
    assert emitted[0] == "job_search_started" and emitted[-1] == "job_search_completed"
    assert all(status in ("running", "completed", "failed") for _, _, status, _ in events)


@pytest.mark.asyncio
async def test_all_searches_failing_reports_structured_error():
    class DeadSearch:
        async def execute(self, query: str, context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
            raise httpx.ConnectError("offline")

    events: List[str] = []

    async def emit(event: str, step: str, status: str, details: str) -> None:
        events.append(event)

    report = (await JobSearchPlugin(_manifest()).execute("python developer jobs in Berlin",
                                                        {"search": DeadSearch(), "emit": emit}))[0]
    assert report["error"].startswith("All search queries failed")
    assert report["jobs"] == [] and events[-1] == "job_search_failed"
    assert "Traceback" not in json.dumps(report)


@pytest.mark.asyncio
async def test_invalid_request_fails_cleanly():
    report = (await JobSearchPlugin(_manifest()).execute("", {"search": FakeSearch()}))[0]
    assert report["error"] == "Describe the jobs you are looking for."


@pytest.mark.asyncio
async def test_dynamic_page_goes_through_existing_playwright_fallback(monkeypatch):
    """A JS-rendered page: trafilatura finds nothing, so the shared crawler's Playwright tier is used."""
    import backend.crawler as crawler_module

    shell = "<html><head><title>Loading…</title></head><body><div id='root'></div></body></html>"
    rendered = ("# SOC Analyst Intern\n\nLocation: Bangalore\n\nAbout the role\n"
                "Monitor SIEM alerts, write Python automation on Linux. Apply below.\n" + "Responsibilities: triage. " * 20)
    response = httpx.Response(200, text=shell, request=httpx.Request("GET", INITECH_URL))
    monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=response))
    playwright = AsyncMock(return_value=rendered)
    monkeypatch.setattr(crawler_module, "limited_playwright_scrape", playwright)

    plugin = JobSearchPlugin(_manifest())
    page = await plugin._fetch_page(INITECH_URL, {})
    assert page["source"] == "playwright" and playwright.await_count == 1
    job = plugin._parse(INITECH_URL, page, NOW, "SOC Analyst Intern")
    assert job is not None and job.raw["crawl_tier"] == "playwright"
    assert job.title == "SOC Analyst Intern" and "SIEM" in job.skills


@pytest.mark.asyncio
async def test_playwright_fallback_enforces_url_guard():
    """The job crawler hands its SSRF guard to the shared Playwright fallback; rejected pages yield nothing."""
    from backend.plugins.playwright_ingestion_plugin import PlaywrightIngestionPlugin

    assert JobSearchPlugin(_manifest())._crawler.url_guard is urls.is_public_url

    async def deny(url: str) -> bool:
        return False

    async def allow_example(url: str) -> bool:
        return url.startswith("https://example.com")

    assert await PlaywrightIngestionPlugin(timeout=20, url_guard=deny).scrape("https://example.com") == ""
    assert "Example Domain" in await PlaywrightIngestionPlugin(timeout=20, url_guard=allow_example).scrape("https://example.com")


@pytest.mark.asyncio
async def test_application_verification_statuses(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return {
            "/open": httpx.Response(200, text="<html><body>Apply for this job</body></html>"),
            "/filled": httpx.Response(200, text="<html><body>This job is no longer accepting applications</body></html>"),
            "/gone": httpx.Response(410),
            "/removed": httpx.Response(404),
            "/forbidden": httpx.Response(403),
            "/moved": httpx.Response(302, headers={"Location": "/careers"}),
            "/careers": httpx.Response(200, text="<html><body>All open roles</body></html>"),
            "/private": httpx.Response(302, headers={"Location": "http://127.0.0.1/"}),
        }[request.url.path]

    real_client = httpx.AsyncClient

    def client_with_mock_transport(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_with_mock_transport)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    plugin = JobSearchPlugin(_manifest())

    async def check(path: str) -> tuple:
        return await plugin._http_check(f"https://{PUBLIC_IP}{path}")

    assert await check("/open") == ("active", True)
    assert await check("/filled") == ("possibly_closed", True)
    assert await check("/gone") == ("closed", True)
    assert await check("/removed") == ("possibly_closed", True)  # 404 twice: likely removed, not proven closed
    assert await check("/forbidden") == ("unknown", False)       # access restricted ≠ closed
    assert await check("/moved") == ("possibly_closed", True)    # redirected to a generic careers page
    assert await check("/private") == ("unknown", False)         # redirect into private network blocked
