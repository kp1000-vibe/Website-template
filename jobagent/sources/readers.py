"""Ways to turn a job posting url into text.

Plain http works for the ATS boards, and fails on exactly the sites that matter
most for a senior search: LinkedIn returns a login wall, Workday returns an empty
react shell, Indeed and Glassdoor block the request outright.

Agent Reach (github.com/Panniantong/Agent-Reach) solves that class of problem by
routing each platform through whatever backend actually works. It deliberately
exposes no stable per platform CLI, so rather than guess at its subcommands we
use the one path it documents and that needs no key, the Jina reader, and leave a
configurable command hook for anyone who has the rest of it installed.

Readers are tried in order and the first one that returns real text wins.
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from .base import TIMEOUT, USER_AGENT, html_to_text

log = logging.getLogger(__name__)

# Enough characters to be a real posting rather than a login wall or an error page.
MIN_USEFUL = 400

# Plain http is a waste of a round trip on these, go straight to a reader.
NEEDS_READER = re.compile(
    r"linkedin\.com|myworkdayjobs\.com|indeed\.com|glassdoor\.com|ziprecruiter\.com"
    r"|icims\.com|taleo\.net|dice\.com|builtin\.com",
    re.I,
)

_TITLE_TAG = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
# The Jina reader prefixes its output with a small header block.
_JINA_TITLE = re.compile(r"^Title:\s*(.+)$", re.M)
_JINA_BODY = re.compile(r"(?s)Markdown Content:\s*(.*)$")


@dataclass
class ReadResult:
    url: str
    title: str
    text: str
    reader: str

    @property
    def useful(self) -> bool:
        return len(self.text) >= MIN_USEFUL


class DirectReader:
    """Plain http. Fast, free, and enough for most applicant tracking systems."""

    name = "direct"

    def read(self, url: str) -> ReadResult | None:
        if NEEDS_READER.search(url):
            return None
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.debug("direct read failed for %s: %s", url, exc)
            return None
        match = _TITLE_TAG.search(resp.text)
        return ReadResult(
            url=url,
            title=html_to_text(match.group(1)) if match else "",
            text=html_to_text(resp.text),
            reader=self.name,
        )


class JinaReader:
    """https://r.jina.ai/<url> returns a page as clean markdown.

    No key, no install, and it gets through the bot blocking that stops a plain
    request. This is the web read path Agent Reach documents.
    """

    name = "jina"
    ENDPOINT = "https://r.jina.ai/"

    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    def read(self, url: str) -> ReadResult | None:
        try:
            resp = requests.get(
                self.ENDPOINT + url,
                headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.debug("jina read failed for %s: %s", url, exc)
            return None
        body = resp.text
        title_match = _JINA_TITLE.search(body)
        body_match = _JINA_BODY.search(body)
        return ReadResult(
            url=url,
            title=(title_match.group(1).strip() if title_match else ""),
            text=(body_match.group(1).strip() if body_match else body.strip()),
            reader=self.name,
        )


class CommandReader:
    """Shell out to whatever you have installed, Agent Reach included.

    Configure it in config.yaml as a template containing {url}, for example:

        sources:
          reader_command: "curl -s https://r.jina.ai/{url}"

    The command must print the page as text on stdout. Because Agent Reach routes
    per platform rather than exposing one documented command, this stays a hook
    you fill in rather than an invocation we guess at.
    """

    name = "command"

    def __init__(self, template: str, timeout: int = 90):
        self.template = template
        self.timeout = timeout

    def read(self, url: str) -> ReadResult | None:
        # shlex.quote keeps a url with a query string from being split or from
        # reaching the shell as separate words.
        cmd = self.template.replace("{url}", shlex.quote(url))
        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=self.timeout
            )
        except (subprocess.SubprocessError, OSError) as exc:
            log.debug("command reader failed for %s: %s", url, exc)
            return None
        if proc.returncode != 0:
            log.debug("command reader exited %s for %s: %s",
                      proc.returncode, url, proc.stderr[:200])
            return None
        body = proc.stdout
        title_match = _JINA_TITLE.search(body)
        return ReadResult(
            url=url,
            title=(title_match.group(1).strip() if title_match else ""),
            text=body.strip(),
            reader=self.name,
        )


def build_chain(names: list[str] | None, command: str | None = None) -> list:
    """Assemble the readers named in config, in order."""
    available = {"direct": DirectReader, "jina": JinaReader}
    chain = []
    for entry in (names or ["direct", "jina"]):
        entry = entry.strip().lower()
        if entry == "command":
            if command:
                chain.append(CommandReader(command))
            else:
                log.warning("reader 'command' is configured but reader_command is not set")
        elif entry in available:
            chain.append(available[entry]())
        else:
            log.warning("unknown reader %r, skipping", entry)
    return chain


def read(url: str, chain: list) -> ReadResult | None:
    """First reader that returns something substantial wins."""
    best: ReadResult | None = None
    for reader in chain:
        result = reader.read(url)
        if result is None:
            continue
        if result.useful:
            log.debug("%s read %s (%d chars)", reader.name, url, len(result.text))
            return result
        # Keep the longest thin result as a last resort rather than losing it.
        if best is None or len(result.text) > len(best.text):
            best = result
    if best:
        log.debug("only thin content for %s via %s", url, best.reader)
    return best


def host_of(url: str) -> str:
    return (urlparse(url).netloc or "").lower()
