"""
job_search_plugin/normalize.py
─────────────────────────────────────────────────────────────────────────────
Deterministic normalisers shared by query parsing, job extraction and ranking:
skills, employment types, work mode, experience, dates, locations, companies
and titles. No network access and no LLM — every value is derived from text.
"""

from __future__ import annotations

import datetime
import re
from typing import Dict, Iterable, List, Optional, Pattern, Tuple

from backend.plugins.job_search_plugin.schema import EmploymentType, ExperienceLevel, WorkMode

# ─────────────────────────────────────────────────────────────────────────────
# Skills
# ─────────────────────────────────────────────────────────────────────────────

# canonical name → lowercase aliases. Ambiguous English words (go, spring, excel,
# react, networking) are deliberately excluded or matched case-sensitively below.
_SKILL_ALIASES: Dict[str, Tuple[str, ...]] = {
    "Python": ("python",),
    "Java": ("java",),
    "JavaScript": ("javascript",),
    "TypeScript": ("typescript",),
    "Go": ("golang",),
    "Rust": ("rust",),
    "C++": ("c++",),
    "C#": ("c#",),
    ".NET": (".net", "dotnet"),
    "SQL": ("sql",),
    "Bash": ("bash", "shell scripting"),
    "PowerShell": ("powershell",),
    "Linux": ("linux",),
    "AWS": ("aws", "amazon web services"),
    "Azure": ("azure",),
    "GCP": ("gcp", "google cloud"),
    "Docker": ("docker",),
    "Kubernetes": ("kubernetes", "k8s"),
    "Terraform": ("terraform",),
    "CI/CD": ("ci/cd", "cicd"),
    "Git": ("git",),
    "SIEM": ("siem",),
    "Splunk": ("splunk",),
    "QRadar": ("qradar",),
    "Microsoft Sentinel": ("microsoft sentinel", "azure sentinel"),
    "ELK": ("elk stack", "elasticsearch"),
    "SOC": ("soc", "security operations center", "security operations centre"),
    "IDS/IPS": ("ids/ips", "intrusion detection", "intrusion prevention"),
    "EDR": ("edr",),
    "Firewalls": ("firewall", "firewalls"),
    "Network Security": ("network security",),
    "Cloud Security": ("cloud security",),
    "Application Security": ("application security", "appsec"),
    "Penetration Testing": ("penetration testing", "pentesting", "pen testing", "vapt"),
    "Vulnerability Management": ("vulnerability management", "vulnerability assessment"),
    "Incident Response": ("incident response",),
    "Threat Intelligence": ("threat intelligence",),
    "Threat Hunting": ("threat hunting",),
    "Malware Analysis": ("malware analysis",),
    "Digital Forensics": ("digital forensics",),
    "OWASP": ("owasp",),
    "Wireshark": ("wireshark",),
    "Nmap": ("nmap",),
    "Burp Suite": ("burp suite", "burpsuite"),
    "Metasploit": ("metasploit",),
    "ISO 27001": ("iso 27001", "iso/iec 27001"),
    "NIST": ("nist",),
    "TCP/IP": ("tcp/ip",),
    "React": ("react.js", "reactjs", "react native"),
    "Node.js": ("node.js", "nodejs"),
    "Django": ("django",),
    "FastAPI": ("fastapi",),
    "Flask": ("flask",),
    "Spring Boot": ("spring boot",),
    "Machine Learning": ("machine learning",),
    "Deep Learning": ("deep learning",),
    "NLP": ("nlp", "natural language processing"),
    "PyTorch": ("pytorch",),
    "TensorFlow": ("tensorflow",),
    "Data Analysis": ("data analysis", "data analytics"),
    "Excel": ("ms excel", "microsoft excel", "advanced excel"),
    "Tableau": ("tableau",),
    "Power BI": ("power bi",),
    "Spark": ("apache spark", "pyspark"),
    "PostgreSQL": ("postgresql", "postgres"),
    "MongoDB": ("mongodb",),
    "Redis": ("redis",),
    "Kafka": ("kafka",),
}

# Proper-noun spellings that are only a skill when capitalised ("React", not "react to alerts").
_CASE_SENSITIVE_SKILLS: Dict[str, Tuple[str, ...]] = {
    "React": ("React",),
    "Excel": ("Excel",),
}


def _term_pattern(term: str, flags: int = re.IGNORECASE) -> Pattern[str]:
    """Match ``term`` as a whole token; symbols like '+', '#', '/' and '.' count as part of it."""
    return re.compile(rf"(?<![\w+#]){re.escape(term)}(?![\w+#])", flags)


def _build_skill_patterns() -> Dict[str, List[Pattern[str]]]:
    patterns: Dict[str, List[Pattern[str]]] = {}
    for canonical, aliases in _SKILL_ALIASES.items():
        patterns[canonical] = [_term_pattern(a) for a in aliases]
    for canonical, spellings in _CASE_SENSITIVE_SKILLS.items():
        patterns.setdefault(canonical, []).extend(_term_pattern(s, flags=0) for s in spellings)
    return patterns


_SKILL_PATTERNS = _build_skill_patterns()
_ALIAS_TO_SKILL = {alias: canonical for canonical, aliases in _SKILL_ALIASES.items() for alias in aliases}
_ALIAS_TO_SKILL.update({canonical.lower(): canonical for canonical in _SKILL_ALIASES})


def find_skills(text: str) -> List[str]:
    """Canonical skills mentioned in ``text``, ordered by first occurrence."""
    if not text:
        return []
    found: List[Tuple[int, str]] = []
    for canonical, patterns in _SKILL_PATTERNS.items():
        positions = [m.start() for p in patterns if (m := p.search(text))]
        if positions:
            found.append((min(positions), canonical))
    return [name for _, name in sorted(found)]


def canonical_skill(name: str) -> str:
    """Map a user-supplied skill to its canonical vocabulary name, or keep it verbatim."""
    return _ALIAS_TO_SKILL.get(name.strip().lower(), name.strip())


def skill_in_text(skill: str, text: str) -> bool:
    """True when ``skill`` (canonical or free-form) is mentioned in ``text``."""
    patterns = _SKILL_PATTERNS.get(canonical_skill(skill))
    if patterns:
        return any(p.search(text) for p in patterns)
    return bool(_term_pattern(skill.strip()).search(text)) if skill.strip() else False


# ─────────────────────────────────────────────────────────────────────────────
# Employment type, work mode, experience
# ─────────────────────────────────────────────────────────────────────────────

_EMPLOYMENT_PATTERNS: Tuple[Tuple[EmploymentType, Pattern[str]], ...] = (
    ("internship", re.compile(r"\bintern(?:ship)?s?\b", re.I)),
    ("part_time", re.compile(r"\bpart[\s_-]?time\b", re.I)),
    ("contract", re.compile(r"\bcontract(?:or|ual)?\b|\bfreelance\b", re.I)),
    ("temporary", re.compile(r"\btemporary\b|\btemp\b", re.I)),
    ("full_time", re.compile(r"\bfull[\s_-]?time\b|\bpermanent\b", re.I)),
)


def find_employment_types(text: str) -> List[EmploymentType]:
    """Every employment type explicitly mentioned in ``text``."""
    return [etype for etype, pattern in _EMPLOYMENT_PATTERNS if pattern.search(text or "")]


def normalize_employment_type(raw: Optional[str]) -> Optional[EmploymentType]:
    """Map a source label ('FULL_TIME', 'Internship', 'Contractor', ...) to one employment type."""
    types = find_employment_types(raw or "")
    return types[0] if types else None


_HYBRID = re.compile(r"\bhybrid\b", re.I)
_REMOTE = re.compile(r"\bremote\b|work\s+from\s+(?:home|anywhere)|\bwfh\b|telecommut", re.I)
_ONSITE = re.compile(r"\bon[\s-]?site\b|\bin[\s-]office\b|work\s+from\s+office", re.I)


def detect_work_mode(text: str) -> Optional[WorkMode]:
    """Structured work mode from short text such as a location label or title."""
    if not text:
        return None
    if _HYBRID.search(text):
        return "hybrid"
    if _REMOTE.search(text):
        return "remote"
    if _ONSITE.search(text):
        return "onsite"
    return None


_YEARS = r"(?:years?|yrs?)"
_NUM = r"(\d{1,2}(?:\.\d)?)"
_EXP_RANGE = re.compile(rf"{_NUM}\s*(?:-|–|—|to)\s*{_NUM}\s*\+?\s*{_YEARS}", re.I)
_EXP_MIN = re.compile(
    rf"{_NUM}\s*\+\s*{_YEARS}"
    rf"|(?:minimum|min\.?|at\s+least|over)\s+(?:of\s+)?{_NUM}\s*{_YEARS}"
    rf"|{_NUM}\s*{_YEARS}\s+(?:of\s+)?(?:\w+\s+){{0,3}}?experience",
    re.I,
)
_EXP_CONTEXT = re.compile(r"experience|\bexp\b", re.I)
_EXP_CONTEXT_WINDOW = 60


def parse_experience(text: str, require_context: bool = True) -> Optional[Tuple[float, Optional[float]]]:
    """Extract an experience requirement as ``(min_years, max_years_or_None)``.

    With ``require_context`` the match must sit near the word 'experience', which
    keeps phrases like '20 years of company history' out of job requirements.
    """
    if not text:
        return None
    for pattern in (_EXP_RANGE, _EXP_MIN):
        for match in pattern.finditer(text):
            if require_context:
                window = text[max(0, match.start() - _EXP_CONTEXT_WINDOW): match.end() + _EXP_CONTEXT_WINDOW]
                if not _EXP_CONTEXT.search(window):
                    continue
            numbers = [float(g) for g in match.groups() if g is not None]
            if pattern is _EXP_RANGE and len(numbers) == 2:
                low, high = sorted(numbers)
                return low, high
            if numbers:
                return numbers[0], None
    return None


_LEVEL_PATTERNS: Tuple[Tuple[ExperienceLevel, Pattern[str]], ...] = (
    ("internship", re.compile(r"\bintern(?:ship)?\b", re.I)),
    ("lead", re.compile(r"\b(?:lead|manager|head|director|principal|vp)\b", re.I)),
    ("senior", re.compile(r"\b(?:senior|sr\.?|staff)\b", re.I)),
    ("entry", re.compile(r"\b(?:junior|jr\.?|entry[\s-]?level|graduate|grad|fresher|trainee|associate)\b", re.I)),
    ("mid", re.compile(r"\bmid[\s-]?(?:level|senior)?\b", re.I)),
)


def detect_level(text: str) -> Optional[ExperienceLevel]:
    """Seniority level explicitly stated in a title or request."""
    for level, pattern in _LEVEL_PATTERNS:
        if pattern.search(text or ""):
            return level
    return None


def infer_level(title: str, min_years: Optional[float], employment: Optional[EmploymentType]) -> Optional[ExperienceLevel]:
    """Seniority from the title first, then the stated minimum experience."""
    if employment == "internship":
        return "internship"
    level = detect_level(title)
    if level:
        return level
    if min_years is None:
        return None
    if min_years <= 1:
        return "entry"
    return "mid" if min_years < 5 else "senior"


# ─────────────────────────────────────────────────────────────────────────────
# Dates
# ─────────────────────────────────────────────────────────────────────────────

def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_date(value: object) -> Optional[datetime.datetime]:
    """Parse an ISO-8601 date/datetime (as used by schema.org) into an aware UTC datetime."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.datetime.combine(datetime.date.fromisoformat(text[:10]), datetime.time())
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


_RELATIVE_POSTED = re.compile(r"\b(\d{1,3})\+?\s*(minute|min|hour|hr|day|week|month)s?\s+ago\b", re.I)
_POSTED_TODAY = re.compile(r"\b(?:just\s+posted|posted\s+today|posted\s+just\s+now)\b", re.I)
_POSTED_YESTERDAY = re.compile(r"\bposted\s+yesterday\b", re.I)
_UNIT_DAYS = {"minute": 1 / 1440, "min": 1 / 1440, "hour": 1 / 24, "hr": 1 / 24, "day": 1, "week": 7, "month": 30}


def parse_relative_posted(text: str, now: datetime.datetime) -> Optional[datetime.datetime]:
    """Estimate a posting date from phrases like 'Posted 3 days ago' or '30+ days ago'."""
    if not text:
        return None
    if _POSTED_TODAY.search(text):
        return now
    if _POSTED_YESTERDAY.search(text):
        return now - datetime.timedelta(days=1)
    match = _RELATIVE_POSTED.search(text)
    if not match:
        return None
    days = int(match.group(1)) * _UNIT_DAYS[match.group(2).lower()]
    return now - datetime.timedelta(days=days)


# ─────────────────────────────────────────────────────────────────────────────
# Locations
# ─────────────────────────────────────────────────────────────────────────────

_LOCATION_ALIASES: Dict[str, str] = {
    "bengaluru": "bangalore",
    "gurugram": "gurgaon",
    "bombay": "mumbai",
    "madras": "chennai",
    "calcutta": "kolkata",
    "new delhi": "delhi",
    "cochin": "kochi",
    "thiruvananthapuram": "trivandrum",
    "nyc": "new york",
    "usa": "united states",
    "u.s.": "united states",
    "us": "united states",
    "uk": "united kingdom",
    "u.k.": "united kingdom",
    "england": "united kingdom",
    "uae": "united arab emirates",
}

_INDIA_TERMS = frozenset({
    "india", "bangalore", "mumbai", "delhi", "hyderabad", "chennai", "pune", "kolkata", "gurgaon",
    "noida", "ahmedabad", "kochi", "trivandrum", "jaipur", "chandigarh", "coimbatore", "indore",
    "kerala", "karnataka", "maharashtra", "tamil nadu", "telangana",
})

# Gazetteer used to spot lowercase locations in free text ("jobs in bangalore").
# "us" is excluded: in prose it is almost always the pronoun.
KNOWN_LOCATIONS = frozenset(_INDIA_TERMS | (set(_LOCATION_ALIASES) - {"us", "u.s."}) | {
    "ireland", "dublin", "cork", "london", "united kingdom", "manchester", "berlin", "germany", "munich",
    "amsterdam", "netherlands", "paris", "france", "singapore", "dubai", "united arab emirates",
    "toronto", "canada", "vancouver", "sydney", "melbourne", "australia", "new york", "san francisco",
    "seattle", "austin", "boston", "united states", "europe", "emea", "apac",
})


def canonical_location(text: str) -> str:
    """Lowercase and replace common aliases (Bengaluru → bangalore) for comparison only."""
    canon = " ".join(re.sub(r"[^\w\s,./-]", " ", (text or "").lower()).split())
    for alias, target in _LOCATION_ALIASES.items():
        canon = re.sub(rf"(?<![\w.]){re.escape(alias)}(?![\w.])", target, canon)
    return canon


def location_matches(wanted: str, job_location_text: str) -> bool:
    """True when the wanted place (city/region/country) appears in the job's location text."""
    want = canonical_location(wanted)
    have = canonical_location(job_location_text)
    if not want:
        return False
    if want == "india" and is_india_location([have]):
        return True  # an Indian city or state satisfies "India" even when the country is omitted
    return re.search(rf"(?<!\w){re.escape(want)}(?!\w)", have) is not None


def is_india_location(locations: Iterable[str]) -> bool:
    return any(term in canonical_location(loc) for loc in locations for term in _INDIA_TERMS)


# ─────────────────────────────────────────────────────────────────────────────
# Companies, titles and tokens (used as deduplication / matching keys)
# ─────────────────────────────────────────────────────────────────────────────

_COMPANY_SUFFIXES = re.compile(
    r"\b(?:inc|llc|ltd|limited|pvt|private|corp|corporation|co|gmbh|plc|llp|ag|sa|bv)\b\.?", re.I
)


def company_key(name: Optional[str]) -> str:
    if not name:
        return ""
    key = _COMPANY_SUFFIXES.sub(" ", name.lower())
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", key).split())


_PHRASE_ALIASES: Tuple[Tuple[Pattern[str], str], ...] = (
    (re.compile(r"\bcyber[\s-]+security\b"), "cybersecurity"),
    (re.compile(r"\binfo[\s-]?sec\b"), "information security"),
    (re.compile(r"\bsr\b\.?"), "senior"),
    (re.compile(r"\bjr\b\.?"), "junior"),
    (re.compile(r"\bswe\b|\bsde\b"), "software engineer"),
)
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "at", "on", "with", "&", "-", "job", "jobs",
    "role", "roles", "position", "opening", "hiring", "remote", "hybrid", "onsite",
})


def normalize_phrase(text: str) -> str:
    """Lowercase, apply phrase aliases (cyber security → cybersecurity) and collapse whitespace."""
    phrase = (text or "").lower()
    for pattern, replacement in _PHRASE_ALIASES:
        phrase = pattern.sub(replacement, phrase)
    return " ".join(re.sub(r"[^a-z0-9+#/ ]", " ", phrase).split())


def tokens(text: str) -> List[str]:
    return [t for t in normalize_phrase(text).replace("/", " ").split() if t not in _STOPWORDS]


def title_key(title: str) -> str:
    """Title reduced to its core: no bracketed notes, no ' - Location' tail, normalised wording."""
    core = re.sub(r"[\(\[].*?[\)\]]", " ", title or "")
    core = re.split(r"\s+[|–—-]\s+", core)[0]
    return " ".join(tokens(core))
