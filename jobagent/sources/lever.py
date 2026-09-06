"""Lever public postings API: https://api.lever.co/v0/postings/{token}?mode=json"""

from __future__ import annotations

import logging
from typing import Iterator

from ..models import Job
from .base import get_json, html_to_text, parse_date

log = logging.getLogger(__name__)
name = "lever"
API = "https://api.lever.co/v0/postings/{token}"


def fetch_company(token: str) -> Iterator[Job]:
    payload = get_json(API.format(token=token), params={"mode": "json"})
    if not isinstance(payload, list):
        return
    for item in payload:
        cats = item.get("categories") or {}
        body = item.get("descriptionPlain") or html_to_text(item.get("description"))
        extras = "\n\n".join(
            html_to_text(l.get("text", "")) + "\n" + html_to_text(l.get("content", ""))
            for l in (item.get("lists") or [])
        )
        yield Job(
            source=name,
            external_id=str(item.get("id")),
            company=token,
            title=item.get("text", ""),
            url=item.get("hostedUrl") or item.get("applyUrl", ""),
            description=(body + "\n\n" + extras).strip(),
            location=cats.get("location", "") or "",
            posted_at=parse_date(item.get("createdAt")),
            remote=(cats.get("commitment", "") or "").lower() == "remote" or None,
            ats="lever",
            raw={"team": cats.get("team"), "commitment": cats.get("commitment")},
        )


def fetch(companies: list[str]) -> Iterator[Job]:
    for token in companies:
        yield from fetch_company(token)
