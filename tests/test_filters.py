"""Filter unit tests. These are the rules that decide what the model never sees,
so a bug here silently costs you jobs."""

from datetime import datetime, timedelta, timezone

from jobagent.filters import TitleFilter, is_fresh, location_ok, seniority
from jobagent.models import Job


def job(**kw) -> Job:
    base = dict(
        source="test", external_id="1", company="Acme", title="Product Manager",
        url="https://example.com/j/1", description="x" * 400, location="",
    )
    base.update(kw)
    return Job(**base)


def test_title_filter_keeps_real_pm_roles():
    tf = TitleFilter()
    for title in [
        "Product Manager", "Senior Product Manager, Payments",
        "Staff Product Manager", "Group Product Manager",
        "Director of Product", "Principal Product Manager, AI",
        "Associate Product Manager", "Technical Product Manager",
        "Head of Product", "Product Owner",
    ]:
        assert tf.matches(title), title


def test_title_filter_drops_lookalikes():
    tf = TitleFilter()
    for title in [
        "Product Marketing Manager", "Senior Product Designer",
        "Technical Program Manager", "Project Manager",
        "Engineering Manager", "Product Analyst", "Product Operations Manager",
        "Product Manager Intern", "Software Engineer",
        "Account Manager", "Product Support Specialist",
    ]:
        assert not tf.matches(title), title


def test_seniority():
    assert seniority("Senior Product Manager") == "senior"
    assert seniority("Associate Product Manager") == "associate"
    assert seniority("Director, Product") == "director"
    assert seniority("Product Manager") == "mid"
    assert seniority("VP Product") == "vp"


def test_freshness():
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    assert is_fresh(job(posted_at=now - timedelta(days=2)), 3, now)
    assert not is_fresh(job(posted_at=now - timedelta(days=9)), 3, now)
    # unknown posted date is kept rather than silently dropped
    assert is_fresh(job(posted_at=None), 3, now)


def test_location_us_only():
    allow = ["San Francisco", "New York", "Remote"]
    assert location_ok(job(location="San Francisco, CA"), allow, False)
    assert location_ok(job(location="Remote - US"), allow, False)
    assert location_ok(job(location="Austin, TX"), allow, False)
    assert not location_ok(job(location="London, UK"), allow, False)
    assert not location_ok(job(location="Bengaluru, India"), allow, False)
    assert not location_ok(job(location="Remote - Canada"), allow, False)


def test_remote_only_mode():
    allow = ["San Francisco"]
    assert location_ok(job(location="Remote"), allow, True)
    assert not location_ok(job(location="San Francisco, CA"), allow, True)


def test_seniority_gate():
    from jobagent.filters import seniority_ok

    allow = ["mid", "senior", "principal", "group", "director"]
    assert seniority_ok("Senior Product Manager", allow)
    assert seniority_ok("Product Manager", allow)
    assert seniority_ok("Principal Product Manager", allow)
    assert seniority_ok("Group Product Manager", allow)
    assert seniority_ok("Director of Product", allow)
    # too junior and too senior both waste an application
    assert not seniority_ok("Associate Product Manager", allow)
    assert not seniority_ok("VP of Product", allow)
    assert not seniority_ok("Chief Product Officer", allow)
