"""Core data types shared across the pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Job:
    """A single posting, normalized across every source."""

    source: str          # "greenhouse" | "lever" | "ashby" | "smartrecruiters" | "manual"
    external_id: str     # id within that source
    company: str
    title: str
    url: str             # canonical posting page (what we open in the browser)
    description: str     # plain text, already stripped of html
    location: str = ""
    posted_at: datetime | None = None
    remote: bool | None = None
    ats: str = ""        # which applicant tracking system hosts the form
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        key = f"{self.source}:{self.company}:{self.external_id}".lower()
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    def age_days(self, now: datetime | None = None) -> float | None:
        if self.posted_at is None:
            return None
        now = now or _utcnow()
        posted = self.posted_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        return (now - posted).total_seconds() / 86400.0

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        d["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        d["id"] = self.id
        return d


@dataclass
class Fit:
    """The scoring verdict for one job against the profile."""

    job_id: str
    score: int                    # 0 to 100
    verdict: str                  # strong | good | stretch | weak
    pitch: str                    # one line on why this is worth applying to
    strengths: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    seniority_match: str = ""     # under | match | over
    model: str = ""
    scored_at: datetime = field(default_factory=_utcnow)


# Application lifecycle.
QUEUED = "queued"        # picked for today, nothing generated yet
PREPPED = "prepped"      # cover letter / answers generated, ready to prefill
PREFILLED = "prefilled"  # form filled in the browser, waiting on your click
APPLIED = "applied"      # you submitted it
SKIPPED = "skipped"      # you passed on it
FAILED = "failed"        # prefill could not complete

TERMINAL = {APPLIED, SKIPPED}
