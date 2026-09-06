"""Cheap, deterministic filters that run before we spend a token on scoring.

The point of this module is to throw away the obvious no, so the model only ever
sees postings that are plausibly product management roles you could take.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import Job

# Titles that are product management. Kept broad on purpose, the model does the
# real judging, this is just the coarse net.
DEFAULT_INCLUDE = [
    r"\bproduct manager\b",
    r"\bproduct management\b",
    r"\bproduct lead\b",
    r"\bproduct owner\b",
    r"\bhead of product\b",
    r"\bdirector,? product\b",
    r"\bdirector of product\b",
    r"\bgroup product manager\b",
    r"\bprincipal product\b",
    r"\bstaff product manager\b",
    r"\bassociate product manager\b",
    r"\bvp,? product\b",
    r"\bvice president,? product\b",
    r"\bchief product officer\b",
    r"\btechnical product manager\b",
    r"\bplatform product manager\b",
    r"\bai product manager\b",
    r"\bgrowth product manager\b",
    r"\bpm\b",
]

# Titles that contain a product management word but are a different job. These
# win over the include list. Product marketing is the classic false positive.
DEFAULT_EXCLUDE = [
    r"\bproduct marketing\b",
    r"\bpmm\b",
    r"\bproduct design(er)?\b",
    r"\bproduct analyst\b",
    r"\bproduct support\b",
    r"\bproduct specialist\b",
    r"\bproduct operations\b",
    r"\bproduct engineer\b",
    r"\bproduct architect\b",
    r"\bproject manager\b",
    r"\bprogram manager\b",
    r"\bengineering manager\b",
    r"\baccount manager\b",
    r"\bsales\b",
    r"\brecruit(er|ing)\b",
    r"\bintern\b",
    r"\bcontract(or)?\b",
    r"\bapprentice\b",
]

SENIORITY_PATTERNS: list[tuple[str, str]] = [
    ("cpo", r"\bchief product officer\b|\bcpo\b"),
    ("vp", r"\bvp\b|\bvice president\b"),
    ("director", r"\bdirector\b|\bhead of product\b"),
    ("group", r"\bgroup product manager\b|\bgpm\b"),
    ("principal", r"\bprincipal\b|\bstaff\b|\blead product manager\b"),
    ("senior", r"\bsenior\b|\bsr\.?\b|\bii\b|\b2\b"),
    ("associate", r"\bassociate\b|\bapm\b|\bjunior\b|\bjr\.?\b|\bentry\b"),
]

REMOTE_HINTS = re.compile(
    r"\bremote\b|\bwork from home\b|\bwfh\b|\bdistributed\b|\banywhere\b", re.I
)
# Remote that is not actually remote for you.
REMOTE_ELSEWHERE = re.compile(
    r"remote\s*[-,(]?\s*(canada|uk|united kingdom|emea|apac|india|europe|latam|brazil|mexico|germany|poland|australia)",
    re.I,
)

_US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT "
    "NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()
US_HINTS = re.compile(
    r"\b(usa|u\.s\.a?\.?|united states|us based|us-based)\b|\b(" + "|".join(_US_STATES) + r")\b",
    re.I,
)
NON_US = re.compile(
    r"\b(canada|toronto|vancouver|london|dublin|berlin|munich|paris|amsterdam|madrid|"
    r"barcelona|lisbon|warsaw|krakow|bangalore|bengaluru|hyderabad|pune|mumbai|delhi|"
    r"gurgaon|noida|singapore|tokyo|sydney|melbourne|tel aviv|sao paulo|mexico city|"
    r"buenos aires|zurich|stockholm|copenhagen|oslo|helsinki|prague|bucharest|manila|"
    r"jakarta|seoul|shanghai|beijing|shenzhen|taipei|dubai|riyadh|cairo|lagos|nairobi|"
    r"cape town|johannesburg)\b",
    re.I,
)


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


class TitleFilter:
    def __init__(self, include: list[str] | None = None, exclude: list[str] | None = None):
        self.include = _compile(include or DEFAULT_INCLUDE)
        self.exclude = _compile(exclude or DEFAULT_EXCLUDE)

    def __call__(self, title: str) -> bool:
        return self.matches(title)

    def matches(self, title: str) -> bool:
        if not title:
            return False
        if any(p.search(title) for p in self.exclude):
            return False
        return any(p.search(title) for p in self.include)


def seniority(title: str) -> str:
    for label, pattern in SENIORITY_PATTERNS:
        if re.search(pattern, title, re.I):
            return label
    return "mid"


def is_fresh(job: Job, max_age_days: int, now: datetime | None = None) -> bool:
    """Unknown posting dates are kept. Dropping them loses real jobs."""
    age = job.age_days(now or datetime.now(timezone.utc))
    if age is None:
        return True
    return -1 <= age <= max_age_days


def location_ok(job: Job, allow: list[str], remote_only: bool) -> bool:
    """US market rules: allow listed metros, allow US remote, drop foreign roles."""
    blob = f"{job.location} {job.title}"
    if REMOTE_ELSEWHERE.search(blob):
        return False
    is_remote = bool(job.remote) or bool(REMOTE_HINTS.search(blob))
    if remote_only:
        return is_remote and not NON_US.search(blob)
    if allow and any(a.lower() in blob.lower() for a in allow):
        return True
    if is_remote:
        return not NON_US.search(blob)
    if NON_US.search(blob):
        return False
    if US_HINTS.search(blob):
        return True
    # No location at all is common on small boards, let the model decide.
    return not job.location.strip()


def company_ok(job: Job, blocked: list[str]) -> bool:
    company = job.company.lower()
    return not any(b in company for b in blocked)


def prefilter(jobs, cfg, title_filter: TitleFilter, now=None):
    """Yield (job, reason_dropped or None) so the caller can report the funnel."""
    for job in jobs:
        if not title_filter.matches(job.title):
            yield job, "title"
        elif not is_fresh(job, cfg.max_age_days, now):
            yield job, "stale"
        elif not company_ok(job, cfg.companies_block):
            yield job, "blocked company"
        elif not location_ok(job, cfg.locations_allow, cfg.remote_only):
            yield job, "location"
        elif len(job.description) < 200:
            yield job, "no description"
        else:
            yield job, None
