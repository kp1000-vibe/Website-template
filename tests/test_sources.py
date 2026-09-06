"""Source adapters, against recorded shapes of each ATS payload.

The live apis are not reachable from CI, so these lock in the parsing: field
names, date formats and the html to text pass. If a board changes its shape,
this is where it shows up.
"""

from datetime import datetime, timedelta, timezone


from jobagent import pipeline
from jobagent.sources import ashby, base, greenhouse, lever

FRESH_DT = datetime.now(timezone.utc) - timedelta(days=1)
FRESH = FRESH_DT.isoformat()
FRESH_MILLIS = int(FRESH_DT.timestamp() * 1000)

GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "id": 4001, "title": "Senior Product Manager, Payments",
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/4001",
            "company_name": "Acme",
            "location": {"name": "San Francisco, CA"},
            "updated_at": FRESH,
            "content": "<p>Own the <b>payments</b> roadmap.</p><ul><li>6 years of product management experience</li><li>Ship pricing and billing surfaces used by "
                       "thousands of merchants</li><li>Partner with engineering, design and data science on quarterly planning</li></ul>",
        },
        {
            "id": 4002, "title": "Product Marketing Manager",
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/4002",
            "company_name": "Acme", "location": {"name": "New York, NY"},
            "updated_at": FRESH,
            "content": "<p>Positioning, launches, messaging and competitive research for the "
                       "payments line. Partner with sales enablement and demand generation on "
                       "campaigns, pricing pages and analyst briefings every quarter.</p>",
        },
    ]
}

LEVER_PAYLOAD = [
    {
        "id": "abc-123", "text": "Product Manager, Data Platform",
        "hostedUrl": "https://jobs.lever.co/acme/abc-123",
        "categories": {"location": "Remote - US", "commitment": "Remote", "team": "Product"},
        "createdAt": FRESH_MILLIS,
        "descriptionPlain": "Own the data platform roadmap end to end, from ingestion through to the semantic layer that powers reporting for every internal team.",
        "lists": [{"text": "What you will do", "content": "<li>Ship things</li><li>Define the quarterly roadmap with engineering</li><li>Run experiments and report on activation</li>"}],
    }
]

ASHBY_PAYLOAD = {
    "jobs": [
        {
            "id": "xyz-9", "title": "Group Product Manager",
            "jobUrl": "https://jobs.ashbyhq.com/acme/xyz-9",
            "organizationName": "Acme", "location": "Remote",
            "publishedAt": FRESH, "isRemote": True,
            "descriptionPlain": "Lead a pod of product managers across billing, identity and the developer platform. You will own hiring, roadmap and the operating cadence.",
        }
    ]
}


def test_html_to_text_keeps_structure():
    text = base.html_to_text("<p>Own the <b>payments</b> roadmap.</p><ul><li>6 years PM</li></ul>")
    assert "Own the payments roadmap." in text
    assert "* 6 years PM" in text
    assert "<" not in text


def test_epoch_millis_dates():
    dt = base.parse_date(1757116800000)
    assert dt is not None and dt.tzinfo is not None
    assert dt.strftime("%Y-%m-%d") == "2025-09-06"


def test_greenhouse_parsing(monkeypatch):
    monkeypatch.setattr(greenhouse, "get_json", lambda *a, **k: GREENHOUSE_PAYLOAD)
    jobs = list(greenhouse.fetch_company("acme"))
    assert len(jobs) == 2
    job = jobs[0]
    assert job.company == "Acme"
    assert job.title == "Senior Product Manager, Payments"
    assert job.location == "San Francisco, CA"
    assert job.ats == "greenhouse"
    assert "payments roadmap" in job.description
    assert job.age_days() < 2


def test_lever_parsing(monkeypatch):
    monkeypatch.setattr(lever, "get_json", lambda *a, **k: LEVER_PAYLOAD)
    job = next(iter(lever.fetch_company("acme")))
    assert job.title == "Product Manager, Data Platform"
    assert job.remote is True
    assert "Ship things" in job.description
    assert job.url.startswith("https://jobs.lever.co/")


def test_ashby_parsing(monkeypatch):
    monkeypatch.setattr(ashby, "get_json", lambda *a, **k: ASHBY_PAYLOAD)
    job = next(iter(ashby.fetch_company("acme")))
    assert job.title == "Group Product Manager"
    assert job.remote is True


def test_dead_board_returns_nothing_rather_than_raising(monkeypatch):
    monkeypatch.setattr(greenhouse, "get_json", lambda *a, **k: None)
    assert list(greenhouse.fetch_company("does-not-exist")) == []


def test_stage_source_fans_out_and_filters(tmp_path, monkeypatch):
    """A dead board in the middle of the list must not lose the good ones."""
    from tests.test_end_to_end import make_cfg

    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(pipeline, "RUNS_DIR", tmp_path / "runs")
    cfg = make_cfg(tmp_path)
    cfg.boards = {"greenhouse": ["acme", "dead", "acme2"], "lever": ["acme"]}

    def fake_gh(url, params=None, retries=2):
        return None if "dead" in url else GREENHOUSE_PAYLOAD

    monkeypatch.setattr(greenhouse, "get_json", fake_gh)
    monkeypatch.setattr(lever, "get_json", lambda *a, **k: LEVER_PAYLOAD)

    store = pipeline.get_store(cfg)
    try:
        result = pipeline.stage_source(cfg, store)
    finally:
        store.close()

    # 2 greenhouse boards x 2 postings + 1 lever posting
    assert result["fetched"] == 5
    # the product marketing ones are dropped on title
    assert result["dropped"]["title"] == 2
    assert result["passed_filters"] == 3
    # both greenhouse boards return the same posting ids, so one is a duplicate
    assert result["new"] == 2
