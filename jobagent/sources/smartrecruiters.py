"""SmartRecruiters public postings API.

List:   https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100
Detail: https://api.smartrecruiters.com/v1/companies/{token}/postings/{id}

The list endpoint carries no description, so we pull detail per posting. To keep
that cheap we only fetch detail for postings whose title survives the title
filter, which the pipeline applies before it ever calls this.
"""

from __future__ import annotations

import logging
from typing import Iterator

from ..models import Job
from .base import get_json, html_to_text, parse_date

log = logging.getLogger(__name__)
name = "smartrecruiters"
LIST_API = "https://api.smartrecruiters.com/v1/companies/{token}/postings"


def _describe(sections: dict) -> str:
    parts = []
    for key in ("jobDescription", "qualifications", "additionalInformation"):
        section = sections.get(key) or {}
        text = html_to_text(section.get("text"))
        if text:
            parts.append(f"{section.get('title', key)}\n{text}")
    return "\n\n".join(parts)


def fetch_company(token: str, title_filter=None) -> Iterator[Job]:
    payload = get_json(LIST_API.format(token=token), params={"limit": 100})
    if not payload or "content" not in payload:
        return
    for item in payload["content"]:
        title = item.get("name", "")
        if title_filter and not title_filter(title):
            continue
        posting_id = item.get("id")
        detail = get_json(f"{LIST_API.format(token=token)}/{posting_id}") or {}
        loc = item.get("location") or {}
        location = ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
        yield Job(
            source=name,
            external_id=str(posting_id),
            company=(item.get("company") or {}).get("name") or token,
            title=title,
            url=detail.get("applyUrl") or item.get("ref", ""),
            description=_describe(detail.get("jobAd", {}).get("sections", {})),
            location=location,
            posted_at=parse_date(item.get("releasedDate")),
            remote=loc.get("remote"),
            ats="smartrecruiters",
            raw={},
        )


def fetch(companies: list[str], title_filter=None) -> Iterator[Job]:
    for token in companies:
        yield from fetch_company(token, title_filter=title_filter)
