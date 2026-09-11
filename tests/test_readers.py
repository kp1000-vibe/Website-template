"""Url readers.

Reading a posting is where sourcing actually breaks, and it breaks per site:
LinkedIn serves a login wall, Workday serves an empty react shell, Indeed blocks
the request. These pin the fallback behaviour without touching the network.
"""

import pytest

from jobagent.sources import manual, readers
from jobagent.sources.readers import (CommandReader, DirectReader, JinaReader,
                                      build_chain)

JINA_BODY = """Title: Senior Product Manager, Billing at Acme Pay

URL Source: https://www.linkedin.com/jobs/view/123/

Markdown Content:
Acme Pay is hiring a Senior Product Manager to own the billing platform.
You will partner with engineering and design on the roadmap, define pricing
surfaces, and run experiments against activation. Six years of B2B SaaS
product management required. This role is hybrid in San Francisco.
""" + ("Additional detail about the team and the interview process. " * 12)

LOGIN_WALL = "Sign in to view this job. Join LinkedIn today."


class FakeResponse:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


def test_direct_reader_refuses_sites_it_cannot_read():
    """No point spending a round trip on a site that always serves a wall."""
    for url in ["https://www.linkedin.com/jobs/view/123/",
                "https://acme.wd1.myworkdayjobs.com/en-US/careers/job/PM_R-1",
                "https://www.indeed.com/viewjob?jk=abc",
                "https://www.glassdoor.com/job-listing/pm-acme-JV_123.htm"]:
        assert DirectReader().read(url) is None, url


def test_direct_reader_handles_a_normal_ats(monkeypatch):
    html = "<html><head><title>Senior PM at Acme</title></head><body><p>" \
           + ("Own the billing platform. " * 40) + "</p></body></html>"
    monkeypatch.setattr(readers.requests, "get", lambda *a, **k: FakeResponse(html))
    result = DirectReader().read("https://job-boards.greenhouse.io/acme/jobs/1")
    assert result is not None
    assert result.title == "Senior PM at Acme"
    assert "billing platform" in result.text
    assert result.useful


def test_jina_reader_parses_the_header_block(monkeypatch):
    monkeypatch.setattr(readers.requests, "get", lambda *a, **k: FakeResponse(JINA_BODY))
    result = JinaReader().read("https://www.linkedin.com/jobs/view/123/")
    assert result.title == "Senior Product Manager, Billing at Acme Pay"
    assert result.text.startswith("Acme Pay is hiring")
    assert "Title:" not in result.text          # header stripped, not left in the body
    assert result.useful


def test_chain_falls_through_from_direct_to_jina(monkeypatch):
    """The whole point: a LinkedIn url that direct cannot touch still gets read."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse(JINA_BODY)

    monkeypatch.setattr(readers.requests, "get", fake_get)
    result = readers.read("https://www.linkedin.com/jobs/view/123/", build_chain(None))
    assert result is not None and result.reader == "jina"
    assert calls == ["https://r.jina.ai/https://www.linkedin.com/jobs/view/123/"]


def test_a_login_wall_is_reported_not_returned_as_a_job(monkeypatch):
    monkeypatch.setattr(readers.requests, "get", lambda *a, **k: FakeResponse(LOGIN_WALL))
    result = readers.read("https://www.linkedin.com/jobs/view/123/", build_chain(None))
    assert result is not None
    assert not result.useful           # kept, but flagged as thin


def test_a_dead_url_returns_nothing(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(readers.requests, "get", boom)
    assert readers.read("https://example.com/job/1", build_chain(None)) is None


def test_command_reader_runs_a_template():
    reader = CommandReader("printf 'Title: PM at Acme\\n\\n%s' " + "'" + "x" * 500 + "'")
    result = reader.read("https://example.com/job/1")
    assert result is not None
    assert result.title == "PM at Acme"
    assert result.useful


def test_command_reader_does_not_let_a_url_break_out_of_the_shell():
    reader = CommandReader("echo {url}")
    result = reader.read("https://example.com/j?a=1&b=2; touch /tmp/pwned")
    assert result is not None
    # the whole thing arrives as one quoted argument, nothing executed
    assert "touch /tmp/pwned" in result.text
    import os
    assert not os.path.exists("/tmp/pwned")


def test_command_reader_survives_a_failing_command():
    assert CommandReader("exit 3").read("https://example.com") is None


def test_build_chain_ignores_unknown_and_unset(caplog):
    assert [r.name for r in build_chain(["direct", "nonsense"])] == ["direct"]
    # "command" without a template is skipped rather than crashing
    assert [r.name for r in build_chain(["command", "jina"])] == ["jina"]
    assert [r.name for r in build_chain(["command"], "curl {url}")] == ["command"]


@pytest.mark.parametrize("raw,title,company", [
    ("Senior Product Manager at Acme Pay", "Senior Product Manager", "Acme Pay"),
    ("Acme Pay hiring Senior Product Manager in San Francisco",
     "Senior Product Manager", "Acme Pay"),
    ("Group Product Manager | Stripe", "Group Product Manager", "Stripe"),
])
def test_title_splitting(raw, title, company):
    assert manual.split_title(raw, "fallback.com") == (title, company)


def test_title_splitting_falls_back_to_the_host():
    assert manual.split_title("", "jobs.acme.com") == ("jobs.acme.com", "jobs.acme.com")


def test_manual_fetch_builds_a_job(monkeypatch):
    monkeypatch.setattr(readers.requests, "get", lambda *a, **k: FakeResponse(JINA_BODY))
    job = manual.fetch_url("https://www.linkedin.com/jobs/view/123/", build_chain(None))
    assert job is not None
    assert job.title == "Senior Product Manager, Billing"
    assert job.company == "Acme Pay"
    assert job.ats == "linkedin"
    assert job.raw["reader"] == "jina"
    assert job.age_days() < 1          # hand pasted links count as fresh
