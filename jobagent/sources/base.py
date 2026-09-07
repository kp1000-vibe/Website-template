"""Shared plumbing for job sources."""

from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Iterator, Protocol

import requests
from dateutil import parser as dateparser

log = logging.getLogger(__name__)

USER_AGENT = "jobagent/0.1 (personal job search; contact via the email in profile.yaml)"
TIMEOUT = 15

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANK = re.compile(r"\n{3,}")


def html_to_text(raw: str | None) -> str:
    """Good enough plain text. Job descriptions are simple markup."""
    if not raw:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "* ", text)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK.sub("\n\n", text).strip()


def parse_date(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        # Lever and Ashby hand back epoch milliseconds.
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    try:
        dt = dateparser.parse(str(value))
    except (ValueError, OverflowError, TypeError):
        return None
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def get_json(url: str, *, params: dict | None = None, retries: int = 1) -> Any | None:
    """GET returning parsed json, or None. A dead board must not kill the run."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            log.debug("%s failed: %s", url, exc)
            if attempt == retries:
                return None
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 404:
            return None            # board token does not exist, nothing to retry
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == retries:
                log.warning("%s gave %s, giving up", url, resp.status_code)
                return None
            time.sleep(2 ** attempt * 2)
            continue
        if not resp.ok:
            log.debug("%s gave %s", url, resp.status_code)
            return None
        try:
            return resp.json()
        except ValueError:
            log.debug("%s returned non json", url)
            return None
    return None


class Source(Protocol):
    name: str

    def fetch(self, companies: list[str]) -> Iterator[Any]:
        ...
