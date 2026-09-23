"""Jobs posted by people, not by applicant tracking systems.

The reason to look here is not volume, it is attribution. A req on a job board
tells you a company is hiring. A post that says "my team is hiring a platform PM,
email me" tells you a company is hiring AND who owns the role AND that they want
to hear from you. One reply to that beats ten applications into a portal.

Two sources work without any account:

  hackernews  the monthly "Ask HN: Who is hiring?" thread, read through the
              Algolia search api. Nearly every comment carries a direct email.
  reddit      public .json endpoints on the hiring subreddits.

X and LinkedIn have no usable unauthenticated search, so they are not faked here.
Point sources.social_command at an Agent Reach install or similar to add them.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
from datetime import datetime, timezone
from typing import Iterator

from ..models import Job
from .base import get_json, html_to_text

log = logging.getLogger(__name__)
name = "social"

HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM = "https://hn.algolia.com/api/v1/items/{id}"
REDDIT_SEARCH = "https://www.reddit.com/r/{sub}/search.json"

DEFAULT_SUBREDDITS = ["ProductManagement", "forhire", "remotejs", "RemoteJobs"]

# A "Who is hiring" comment nearly always opens with the company, then the role,
# then the location, separated by pipes or bars.
# A "Who is hiring" comment opens with a header line in the house style:
#     Company | Role | Location | Full time | $range
# Fields vary and several are often missing, so each segment is classified rather
# than read positionally, and only the first line is treated as the header.
_SEP = re.compile(r"\s*[|\u2013\u2014]\s*|\s+-\s+")
_REMOTE = re.compile(r"\bremote\b", re.I)
_LOCATION_HINT = re.compile(
    r"\bremote\b|\bonsite\b|\bhybrid\b|\b(?:SF|NYC|LA|SEA|ATX|USA?|UK|EU)\b"
    r"|[A-Z][a-z]+,\s*[A-Z]{2}\b", re.I)
_EMPLOYMENT_HINT = re.compile(
    r"\bfull[\s-]?time\b|\bpart[\s-]?time\b|\bcontract\b|\bintern(ship)?\b"
    r"|\bvisa\b|\bh1b\b|\$|\bequity\b|\bsalary\b|\bonsite only\b", re.I)


def parse_hn_header(text: str, title_filter=None) -> tuple[str, str, str]:
    """Return (company, title, location) from a Who is hiring comment.

    Reading positionally breaks on the first comment that omits a field, and the
    naive version of this produced a title that was the header and the opening
    paragraph run together. So classify each segment instead.
    """
    first_line = (text.strip().splitlines() or [""])[0]
    segments = [seg.strip() for seg in _SEP.split(first_line) if seg.strip()]
    if not segments:
        return "", "", ""

    company = segments[0][:80]
    title, location = "", ""
    for seg in segments[1:]:
        if not location and _LOCATION_HINT.search(seg) and len(seg) < 60:
            location = seg[:60]
        elif not title and not _EMPLOYMENT_HINT.search(seg) and len(seg) < 90:
            title = seg

    if not title and title_filter:
        # Header carried no role, so take the one matching line from the body
        # rather than everything from that point on.
        for line in text.splitlines()[1:]:
            line = line.strip()
            if line and title_filter(line) and len(line) < 120:
                title = line
                break
    return company, title, location


def _is_recent(timestamp: int | None, max_age_days: int) -> bool:
    if not timestamp:
        return True
    age = (datetime.now(timezone.utc).timestamp() - timestamp) / 86400
    return age <= max_age_days


def find_whoishiring_thread() -> int | None:
    """The newest 'Ask HN: Who is hiring?' story id."""
    payload = get_json(HN_SEARCH, params={
        "query": "Ask HN: Who is hiring?",
        "tags": "story,author_whoishiring",
        "hitsPerPage": 1,
    })
    hits = (payload or {}).get("hits") or []
    return hits[0].get("objectID") if hits else None


def fetch_hackernews(title_filter=None, max_age_days: int = 35) -> Iterator[Job]:
    """Top level comments of the current Who is hiring thread."""
    story_id = find_whoishiring_thread()
    if not story_id:
        log.debug("could not find a Who is hiring thread")
        return
    thread = get_json(HN_ITEM.format(id=story_id))
    if not thread:
        return
    for comment in thread.get("children") or []:
        body = html_to_text(comment.get("text") or "")
        if not body or len(body) < 120:
            continue
        company, title, location = parse_hn_header(body, title_filter)
        company = company or comment.get("author") or "unknown"
        if title_filter and not (title and title_filter(title)):
            continue
        created = comment.get("created_at_i")
        if not _is_recent(created, max_age_days):
            continue
        yield Job(
            source="hackernews",
            external_id=str(comment.get("id")),
            company=company[:80],
            title=title,
            url=f"https://news.ycombinator.com/item?id={comment.get('id')}",
            description=body,
            location=location or ("Remote" if _REMOTE.search(body) else ""),
            posted_at=(datetime.fromtimestamp(created, tz=timezone.utc) if created else None),
            remote=bool(_REMOTE.search(location or body)),
            ats="none",
            raw={"poster": comment.get("author"), "platform": "hackernews"},
        )


def fetch_reddit(subreddits: list[str] | None = None, title_filter=None,
                 max_age_days: int = 7) -> Iterator[Job]:
    for sub in (subreddits or DEFAULT_SUBREDDITS):
        payload = get_json(REDDIT_SEARCH.format(sub=sub), params={
            "q": "hiring product manager",
            "restrict_sr": "1",
            "sort": "new",
            "t": "month",
            "limit": 50,
        })
        for child in ((payload or {}).get("data") or {}).get("children") or []:
            post = child.get("data") or {}
            title = (post.get("title") or "").strip()
            body = post.get("selftext") or ""
            if title_filter and not title_filter(title) and not any(
                title_filter(line) for line in body.splitlines()
            ):
                continue
            created = post.get("created_utc")
            if not _is_recent(int(created) if created else None, max_age_days):
                continue
            text = f"{title}\n\n{body}".strip()
            if len(text) < 120:
                continue
            yield Job(
                source="reddit",
                external_id=str(post.get("id")),
                company=(post.get("link_flair_text") or f"r/{sub}")[:80],
                title=title[:140],
                url="https://www.reddit.com" + (post.get("permalink") or ""),
                description=text,
                location="Remote" if _REMOTE.search(text) else "",
                posted_at=(datetime.fromtimestamp(created, tz=timezone.utc) if created else None),
                remote=bool(_REMOTE.search(text)),
                ats="none",
                raw={"poster": post.get("author"), "platform": "reddit", "subreddit": sub},
            )


def fetch_command(template: str, query: str, timeout: int = 120) -> Iterator[Job]:
    """Whatever you have that can search X or LinkedIn, behind one hook.

    The command gets {query} substituted and must print a json list of objects
    with at least: id, author, text, url, and optionally created_at (iso or epoch).
    X needs cookies and LinkedIn has no open search, so this is a template you
    fill in rather than an invocation guessed at.
    """
    cmd = template.replace("{query}", shlex.quote(query))
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("social_command failed: %s", exc)
        return
    if proc.returncode != 0:
        log.warning("social_command exited %s: %s", proc.returncode, proc.stderr[:200])
        return
    try:
        items = json.loads(proc.stdout)
    except ValueError:
        log.warning("social_command did not print json")
        return
    if not isinstance(items, list):
        log.warning("social_command printed json that is not a list")
        return

    for item in items:
        if not isinstance(item, dict):
            continue
        text = item.get("text") or ""
        if len(text) < 60:
            continue
        created = item.get("created_at")
        posted = None
        if isinstance(created, (int, float)):
            posted = datetime.fromtimestamp(created, tz=timezone.utc)
        elif isinstance(created, str):
            from .base import parse_date
            posted = parse_date(created)
        yield Job(
            source="social",
            external_id=str(item.get("id") or item.get("url") or text[:40]),
            company=str(item.get("company") or item.get("author") or "unknown")[:80],
            title=str(item.get("title") or text.split("\n")[0])[:140],
            url=str(item.get("url") or ""),
            description=text,
            location="Remote" if _REMOTE.search(text) else "",
            posted_at=posted,
            ats="none",
            raw={"poster": item.get("author"), "platform": item.get("platform") or "command"},
        )
