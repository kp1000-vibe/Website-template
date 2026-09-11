"""Jobs you found yourself.

Drop urls into config/manual_urls.txt (one per line, # comments allowed) and the
pipeline treats them like any other candidate: scored, queued, prefilled. This is
the escape hatch for LinkedIn, Workday and anything else without an open feed.

Reading those pages is the hard part, so it is delegated to readers.py, which
tries plain http first and falls back to a reader that can get through a login
wall or a javascript shell.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from ..models import Job
from . import readers

log = logging.getLogger(__name__)
name = "manual"

_ATS_HINTS = {
    "greenhouse": ("greenhouse.io", "job-boards.greenhouse.io"),
    "lever": ("jobs.lever.co",),
    "ashby": ("jobs.ashbyhq.com",),
    "workday": ("myworkdayjobs.com",),
    "smartrecruiters": ("jobs.smartrecruiters.com",),
    "icims": ("icims.com",),
    "linkedin": ("linkedin.com",),
}

# "Senior Product Manager - Acme" / "Acme hiring Senior PM" / "Senior PM at Acme"
_TITLE_SPLITTERS = [
    re.compile(r"^(?P<title>.+?)\s+at\s+(?P<company>.+?)(?:\s*[|–—-].*)?$", re.I),
    re.compile(r"^(?P<company>.+?)\s+hiring\s+(?P<title>.+?)(?:\s+in\s+.*)?$", re.I),
    re.compile(r"^(?P<title>.+?)\s*[|–—]\s*(?P<company>.+)$"),
]


def detect_ats(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    for ats, needles in _ATS_HINTS.items():
        if any(n in host for n in needles):
            return ats
    return "unknown"


def split_title(raw: str, fallback_host: str) -> tuple[str, str]:
    """Pull a job title and a company out of a page title."""
    cleaned = re.sub(r"\s+", " ", raw or "").strip()
    for pattern in _TITLE_SPLITTERS:
        match = pattern.match(cleaned)
        if match:
            title = match.group("title").strip(" -|–—")
            company = match.group("company").strip(" -|–—")
            if title and company:
                return title, company
    return cleaned or fallback_host, fallback_host


def fetch_url(url: str, chain: list | None = None) -> Job | None:
    chain = chain if chain is not None else readers.build_chain(None)
    result = readers.read(url, chain)
    if result is None:
        log.warning("could not read %s with any reader", url)
        return None
    if not result.useful:
        log.warning("%s returned only %d characters for %s, which usually means a "
                    "login wall. Add 'jina' to sources.readers in config.yaml if it "
                    "is not already there.", result.reader, len(result.text), url)

    host = urlparse(url).netloc
    title, company = split_title(result.title, host)
    return Job(
        source=name,
        external_id=url,
        company=company,
        title=title,
        url=url,
        description=result.text[:20000],
        # We cannot know when a hand pasted link was posted. Treat it as today so
        # the freshness filter does not silently drop something you chose yourself.
        posted_at=datetime.now(timezone.utc),
        ats=detect_ats(url),
        raw={"reader": result.reader},
    )


def fetch(path: Path, chain: list | None = None) -> Iterator[Job]:
    if not path.exists():
        return
    chain = chain if chain is not None else readers.build_chain(None)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line.startswith("http"):
            continue
        job = fetch_url(line, chain)
        if job:
            yield job
