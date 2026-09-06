"""Ashby public job board API.

https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true
"""

from __future__ import annotations

import logging
from typing import Iterator

from ..models import Job
from .base import get_json, html_to_text, parse_date

log = logging.getLogger(__name__)
name = "ashby"
API = "https://api.ashbyhq.com/posting-api/job-board/{token}"


def fetch_company(token: str) -> Iterator[Job]:
    payload = get_json(API.format(token=token), params={"includeCompensation": "true"})
    if not payload or "jobs" not in payload:
        return
    for item in payload["jobs"]:
        yield Job(
            source=name,
            external_id=str(item.get("id")),
            company=item.get("organizationName") or token,
            title=item.get("title", ""),
            url=item.get("jobUrl") or item.get("applyUrl", ""),
            description=item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml")),
            location=item.get("location", "") or "",
            posted_at=parse_date(item.get("publishedAt") or item.get("updatedAt")),
            remote=item.get("isRemote"),
            ats="ashby",
            raw={"compensation": item.get("compensation"), "team": item.get("team")},
        )


def fetch(companies: list[str]) -> Iterator[Job]:
    for token in companies:
        yield from fetch_company(token)
