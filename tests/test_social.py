"""Hacker News and Reddit sources, against recorded payload shapes.

Neither api is reachable from CI, so these lock in the parsing and the rule that
matters most: a hiring post is only useful if it survives the freshness filter,
and the HN thread is monthly rather than daily.
"""

from datetime import datetime, timedelta, timezone

from jobagent.filters import TitleFilter, max_age_for
from jobagent.sources import social
from jobagent.sources.social import parse_hn_header

NOW = datetime.now(timezone.utc)
RECENT = int((NOW - timedelta(days=9)).timestamp())
ANCIENT = int((NOW - timedelta(days=200)).timestamp())

HN_STORY = {"hits": [{"objectID": "44100000"}]}
HN_THREAD = {
    "children": [
        {
            "id": 44100001, "author": "janemcleod", "created_at_i": RECENT,
            "text": "Acme Pay | Senior Product Manager, Platform | SF or Remote (US)"
                    "<p>We are building the billing platform and need someone with "
                    "enterprise integration experience across Salesforce and Oracle. "
                    "You would own the roadmap end to end.</p>"
                    "<p>Email me at jane.mcleod@acmepay.com</p>",
        },
        {   # right shape, wrong job
            "id": 44100002, "author": "bobsmith", "created_at_i": RECENT,
            "text": "Beta Corp | Senior Backend Engineer | Remote"
                    "<p>Go and Postgres. We need someone to own the ingestion "
                    "pipeline and its reliability budget. Email bob@betacorp.com</p>",
        },
        {   # right job, but from a thread months ago
            "id": 44100003, "author": "oldpost", "created_at_i": ANCIENT,
            "text": "Gamma | Senior Product Manager | NYC"
                    "<p>A good role that was posted a very long time ago indeed, "
                    "and should not reach today's shortlist at all.</p>",
        },
    ]
}

REDDIT = {
    "data": {"children": [
        {"data": {
            "id": "abc123", "title": "[Hiring] Senior Product Manager, Platform at Acme",
            "selftext": "We are hiring a senior PM to own our integrations platform. "
                        "Remote in the US. DM me or email jane@acme.com with questions.",
            "permalink": "/r/ProductManagement/comments/abc123/hiring/",
            "created_utc": float(RECENT), "author": "janemcleod",
            "link_flair_text": "Hiring",
        }},
        {"data": {
            "id": "def456", "title": "How do you run a roadmap review?",
            "selftext": "Curious what cadence people use for roadmap reviews and "
                        "whether you involve engineering leads in the first pass.",
            "permalink": "/r/ProductManagement/comments/def456/roadmap/",
            "created_utc": float(RECENT), "author": "someone",
        }},
    ]}
}


def test_hackernews_parses_a_who_is_hiring_comment(monkeypatch):
    def fake(url, params=None, retries=1):
        return HN_STORY if "search_by_date" in url else HN_THREAD

    monkeypatch.setattr(social, "get_json", fake)
    jobs = list(social.fetch_hackernews(TitleFilter(), max_age_days=35))

    assert len(jobs) == 1                      # engineer and stale post both dropped
    job = jobs[0]
    assert job.company == "Acme Pay"
    assert "Product Manager" in job.title
    assert job.remote is True
    assert job.url == "https://news.ycombinator.com/item?id=44100001"
    assert job.raw["poster"] == "janemcleod"
    assert "jane.mcleod@acmepay.com" in job.description


def test_hackernews_survives_a_missing_thread(monkeypatch):
    monkeypatch.setattr(social, "get_json", lambda *a, **k: None)
    assert list(social.fetch_hackernews(TitleFilter())) == []


def test_reddit_keeps_hiring_posts_and_drops_discussion(monkeypatch):
    monkeypatch.setattr(social, "get_json", lambda *a, **k: REDDIT)
    jobs = list(social.fetch_reddit(["ProductManagement"], TitleFilter(), max_age_days=35))
    assert len(jobs) == 1
    assert jobs[0].raw["poster"] == "janemcleod"
    assert jobs[0].url.endswith("/r/ProductManagement/comments/abc123/hiring/")


def test_a_monthly_thread_is_not_judged_by_a_three_day_window():
    """The whole point. A req goes stale in days, the HN thread is monthly, and
    using one window for both silently discards every hiring post."""
    from jobagent.models import Job

    class Cfg:
        max_age_days = 3
        social_max_age_days = 35

    board_job = Job(source="greenhouse", external_id="1", company="A", title="PM",
                    url="u", description="d")
    hn_job = Job(source="hackernews", external_id="2", company="B", title="PM",
                 url="u", description="d")
    assert max_age_for(board_job, Cfg()) == 3
    assert max_age_for(hn_job, Cfg()) == 35


def test_command_source_reads_json_from_any_tool():
    """X and LinkedIn go through this hook rather than a guessed invocation."""
    payload = (
        '[{"id":"1","author":"@kpatro","platform":"x",'
        '"text":"My team is hiring a Staff PM for enterprise integrations. DM me. '
        'We are remote friendly across the US and move fast.",'
        '"url":"https://x.com/kpatro/status/1"}]'
    )
    jobs = list(social.fetch_command(f"printf '%s' '{payload}'", "staff pm"))
    assert len(jobs) == 1
    assert jobs[0].raw["poster"] == "@kpatro"
    assert jobs[0].raw["platform"] == "x"
    assert jobs[0].url == "https://x.com/kpatro/status/1"


def test_command_source_survives_junk_output():
    assert list(social.fetch_command("echo 'not json'", "q")) == []
    assert list(social.fetch_command("exit 1", "q")) == []
    assert list(social.fetch_command("echo '{}'", "q")) == []      # json, but not a list


def test_hn_header_fields_are_classified_not_counted():
    """Reading segments positionally breaks on the first comment that omits one,
    and the naive version ran the header into the opening paragraph."""
    tf = TitleFilter()
    cases = [
        ("Acme Pay | Senior Product Manager, Platform | SF or Remote (US) | Full time",
         ("Acme Pay", "Senior Product Manager, Platform", "SF or Remote (US)")),
        ("Gamma Robotics | Staff Product Manager | New York, NY | $200k-$260k | Full time",
         ("Gamma Robotics", "Staff Product Manager", "New York, NY")),
        ("Epsilon Inc - Senior Product Manager - Austin, TX",
         ("Epsilon Inc", "Senior Product Manager", "Austin, TX")),
        # location before role, which positional parsing gets backwards
        ("Zeta | Remote | Senior Product Manager",
         ("Zeta", "Senior Product Manager", "Remote")),
    ]
    for header, expected in cases:
        assert parse_hn_header(header, tf) == expected, header


def test_hn_header_falls_back_to_the_body_for_a_role():
    text = "Acme Pay\nWe are hiring a Senior Product Manager for the platform team."
    company, title, _ = parse_hn_header(text, TitleFilter())
    assert company == "Acme Pay"
    assert "Senior Product Manager" in title
    assert len(title) < 120          # one line, not the rest of the comment


def test_hn_header_survives_an_empty_comment():
    assert parse_hn_header("", TitleFilter()) == ("", "", "")
