"""Jobs you found yourself.

Drop urls into config/manual_urls.txt (one per line, # comments allowed) and the
pipeline treats them like any other candidate: scored, queued, prefilled. This is
the escape hatch for LinkedIn or anything else we do not scrape.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

import requests

from ..models import Job
from .base import TIMEOUT, USER_AGENT, html_to_text

log = logging.getLogger(__name__)
name = "manual"

_TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
_ATS_HINTS = {
    "greenhouse": ("greenhouse.io", "job-boards.greenhouse.io"),
    "lever": ("jobs.lever.co",),
    "ashby": ("jobs.ashbyhq.com",),
    "workday": ("myworkdayjobs.com",),
    "smartrecruiters": ("jobs.smartrecruiters.com",),
    "icims": ("icims.com",),
    "linkedin": ("linkedin.com",),
}


def detect_ats(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    for ats, needles in _ATS_HINTS.items():
        if any(n in host for n in needles):
            return ats
    return "unknown"


def fetch_url(url: str) -> Job | None:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("could not read %s: %s", url, exc)
        return None
    body = resp.text
    match = _TITLE.search(body)
    title = html_to_text(match.group(1)) if match else url
    host = urlparse(url).netloc
    company = title.split(" at ")[-1] if " at " in title else host
    return Job(
        source=name,
        external_id=url,
        company=company.strip() or host,
        title=title.strip(),
        url=url,
        description=html_to_text(body)[:20000],
        # We cannot know when a hand pasted link was posted. Treat it as today so
        # the freshness filter does not silently drop something you chose yourself.
        posted_at=datetime.now(timezone.utc),
        ats=detect_ats(url),
        raw={},
    )


def fetch(path: Path) -> Iterator[Job]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line.startswith("http"):
            continue
        job = fetch_url(line)
        if job:
            yield job
