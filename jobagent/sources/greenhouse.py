"""Greenhouse public job board API.

Docs: https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
Public, unauthenticated, and returns the full description plus updated_at.
"""

from __future__ import annotations

import logging
from typing import Iterator

from ..models import Job
from .base import get_json, html_to_text, parse_date

log = logging.getLogger(__name__)
name = "greenhouse"
API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def fetch_company(token: str) -> Iterator[Job]:
    payload = get_json(API.format(token=token), params={"content": "true"})
    if not payload or "jobs" not in payload:
        return
    for item in payload["jobs"]:
        offices = item.get("offices") or []
        location = (item.get("location") or {}).get("name") or ", ".join(
            o.get("name", "") for o in offices
        )
        yield Job(
            source=name,
            external_id=str(item.get("id")),
            company=item.get("company_name") or token,
            title=item.get("title", ""),
            url=item.get("absolute_url", ""),
            description=html_to_text(item.get("content")),
            location=location or "",
            # Greenhouse exposes updated_at on the list endpoint; first_published
            # shows up on some boards and is the better signal when present.
            posted_at=parse_date(item.get("first_published") or item.get("updated_at")),
            ats="greenhouse",
            raw={"metadata": item.get("metadata")},
        )


def fetch(companies: list[str]) -> Iterator[Job]:
    for token in companies:
        count = 0
        for job in fetch_company(token):
            count += 1
            yield job
        log.debug("greenhouse/%s: %d postings", token, count)
