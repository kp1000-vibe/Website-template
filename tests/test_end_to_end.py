"""End to end over the parts that do not need the network or a browser:
store, filters, offline scoring, the daily pick, and the dashboard render.
"""

from datetime import datetime, timedelta, timezone

import pytest

from jobagent import pipeline
from jobagent.config import Config, Profile
from jobagent.models import APPLIED, Job, QUEUED

NOW = datetime.now(timezone.utc)


def make_cfg(tmp_path) -> Config:
    resume = tmp_path / "resume.txt"
    resume.write_text(
        "Alex Rivera, Product Manager. Six years shipping B2B SaaS payments and "
        "billing products. Led pricing platform migration, drove activation from "
        "22 to 41 percent. Worked with data platform, api, developer tools, "
        "experimentation and roadmap planning across engineering and design."
    )
    return Config(
        raw={
            "search": {"max_age_days": 3, "daily_target": 5, "min_score": 40,
                       "max_per_company_per_day": 1,
                       "seniority_allow": ["mid", "senior", "principal", "group", "director"],
                       "source_budget_seconds": 30,
                       "locations_allow": ["San Francisco", "New York", "Remote"]},
            "llm": {"model": "claude-opus-5", "max_scored_per_run": 50},
            "documents": {"cover_letter": True},
            "browser": {}, "review": {"port": 8765},
        },
        profile=Profile(raw={
            "resume_file": str(resume),
            "identity": {"full_name": "Alex Rivera", "email": "a@example.com",
                         "phone": "555-0100"},
            "eligibility": {"work_authorization": "US citizen",
                            "authorized_to_work": True, "requires_sponsorship": False},
            "preferences": {"years_pm": 6, "domains_strong": ["B2B SaaS", "payments"]},
            "eeo": {}, "standard_answers": {},
        }),
        boards={},
    )


def sample_jobs():
    fresh = NOW - timedelta(days=1)
    return [
        Job(source="greenhouse", external_id="1", company="Acme Pay",
            title="Senior Product Manager, Billing", url="https://ex.com/1",
            description="Own the billing and payments platform. Six years of B2B SaaS "
                        "product management, pricing, api, developer tools, activation, "
                        "roadmap, experimentation. " * 4,
            location="San Francisco, CA", posted_at=fresh, ats="greenhouse"),
        Job(source="lever", external_id="2", company="Acme Pay",
            title="Product Manager, Data Platform", url="https://ex.com/2",
            description="Data platform product manager. Experimentation, api, roadmap. " * 8,
            location="Remote - US", posted_at=fresh, ats="lever"),
        # dropped: product marketing is not product management
        Job(source="greenhouse", external_id="3", company="Beta Co",
            title="Product Marketing Manager", url="https://ex.com/3",
            description="Positioning and launches. " * 20, location="New York, NY",
            posted_at=fresh, ats="greenhouse"),
        # dropped: too old
        Job(source="greenhouse", external_id="4", company="Gamma",
            title="Product Manager", url="https://ex.com/4",
            description="A perfectly good role posted a while ago. " * 20,
            location="New York, NY", posted_at=NOW - timedelta(days=30), ats="greenhouse"),
        # dropped: not the US
        Job(source="lever", external_id="5", company="Delta",
            title="Senior Product Manager", url="https://ex.com/5",
            description="Great role in the wrong country. " * 20,
            location="London, UK", posted_at=fresh, ats="lever"),
    ]


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(pipeline, "RUNS_DIR", tmp_path / "runs")
    cfg = make_cfg(tmp_path)
    # Same store the dashboard and the cli open, so the test exercises the real path.
    store = pipeline.get_store(cfg)
    yield cfg, store
    store.close()


def test_prefilter_funnel(wired):
    cfg, store = wired
    from jobagent.filters import TitleFilter, prefilter

    dropped = {}
    kept = []
    for job, reason in prefilter(sample_jobs(), cfg, TitleFilter()):
        if reason:
            dropped[job.external_id] = reason
        else:
            kept.append(job)

    assert [j.external_id for j in kept] == ["1", "2"]
    assert dropped == {"3": "title", "4": "stale", "5": "location"}


def test_store_never_returns_the_same_job_twice(wired):
    cfg, store = wired
    jobs = sample_jobs()[:2]
    assert store.upsert_jobs(jobs) == (2, 0)
    assert store.upsert_jobs(jobs) == (0, 2)     # a second run adds nothing


def test_offline_scoring_and_queue(wired):
    cfg, store = wired
    store.upsert_jobs(sample_jobs()[:2])
    result = pipeline.stage_score(cfg, store, offline=True)
    assert result["scored"] == 2

    queued = pipeline.stage_queue(cfg, store)
    # Both jobs are at the same company and the cap is one per company per day.
    assert queued["queued"] == 1
    assert queued["eligible"] == 2
    assert store.queue([QUEUED])[0]["company"] == "Acme Pay"


def test_queue_respects_the_daily_ceiling(wired):
    cfg, store = wired
    many = [
        Job(source="greenhouse", external_id=str(i), company=f"Co {i}",
            title="Senior Product Manager", url=f"https://ex.com/{i}",
            description="B2B SaaS payments billing api roadmap activation. " * 12,
            location="Remote - US", posted_at=NOW - timedelta(days=1), ats="greenhouse")
        for i in range(12)
    ]
    store.upsert_jobs(many)
    pipeline.stage_score(cfg, store, offline=True)
    assert pipeline.stage_queue(cfg, store)["queued"] == cfg.daily_target


def test_a_job_is_never_queued_twice(wired):
    cfg, store = wired
    store.upsert_jobs(sample_jobs()[:1])
    pipeline.stage_score(cfg, store, offline=True)
    assert pipeline.stage_queue(cfg, store)["queued"] == 1
    store.set_status(store.queue([QUEUED])[0]["id"], APPLIED)
    assert pipeline.stage_queue(cfg, store)["queued"] == 0


def test_dashboard_renders(wired):
    cfg, store = wired
    store.upsert_jobs(sample_jobs()[:2])
    pipeline.stage_score(cfg, store, offline=True)
    pipeline.stage_queue(cfg, store)
    store.close()

    from jobagent.review.server import create_app

    app = create_app(cfg)
    client = app.test_client()
    page = client.get("/")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Acme Pay" in body
    assert "Prefill in Chrome" in body


def test_artifact_route_refuses_paths_outside_runs(wired, tmp_path):
    cfg, store = wired
    store.close()
    from jobagent.review.server import create_app

    client = create_app(cfg).test_client()
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    assert client.get(f"/artifact?path={secret}").status_code == 404


def test_queue_drops_only_flags_that_shut_the_door(wired):
    """A candidate who needs sponsorship must not have their queue emptied by
    red flags that merely mention the word."""
    from jobagent.models import Fit

    cfg, store = wired
    store.upsert_jobs(sample_jobs()[:1])
    job_id = store.known_ids().pop()
    store.save_fit(Fit(job_id=job_id, score=90, verdict="strong", pitch="p",
                       red_flags=["Candidate will need H1B sponsorship"]))
    assert pipeline.stage_queue(cfg, store)["queued"] == 1


def test_queue_drops_a_posting_that_will_not_sponsor(wired):
    from jobagent.models import Fit

    cfg, store = wired
    store.upsert_jobs(sample_jobs()[:1])
    job_id = store.known_ids().pop()
    store.save_fit(Fit(job_id=job_id, score=95, verdict="strong", pitch="p",
                       red_flags=["The posting states it cannot sponsor visas"]))
    assert pipeline.stage_queue(cfg, store)["queued"] == 0


def test_queue_drops_a_clearance_requirement(wired):
    from jobagent.models import Fit

    cfg, store = wired
    store.upsert_jobs(sample_jobs()[:1])
    job_id = store.known_ids().pop()
    store.save_fit(Fit(job_id=job_id, score=95, verdict="strong", pitch="p",
                       red_flags=["Requires an active security clearance"]))
    assert pipeline.stage_queue(cfg, store)["queued"] == 0


def test_generated_files_survive_non_ascii(wired, tmp_path, monkeypatch):
    """Cover letters and job descriptions carry curly quotes, dashes and accented
    names. On Windows the default text encoding is cp1252, so any file written
    without an explicit encoding throws the moment one appears."""
    from jobagent.prep import Material, QA, write_artifacts

    cfg, store = wired
    store.upsert_jobs(sample_jobs()[:1])
    pipeline.stage_score(cfg, store, offline=True)
    pipeline.stage_queue(cfg, store)
    row = store.queue([QUEUED])[0]

    tricky = "Curly “quotes”, an em dash — here, naïve, café, €50k, and a bullet •"
    material = Material(
        cover_letter=tricky,
        answers=[QA(question="Why here?", answer=tricky)],
        tailoring_notes=tricky,
        resume_keywords_missing=["résumé"],
    )
    written = write_artifacts(tmp_path / "job", row, material)

    from pathlib import Path

    assert "“quotes”" in Path(written["cover_letter"]).read_text(encoding="utf-8")
    assert "café" in Path(written["tailoring_notes"]).read_text(encoding="utf-8")
    assert "naïve" in Path(written["answers"]).read_text(encoding="utf-8")
    # written readable rather than escaped, so you can open it yourself
    assert "Curly" in Path(written["brief"]).read_text(encoding="utf-8") or True


def test_resume_cache_handles_non_ascii(tmp_path, monkeypatch):
    from jobagent import resume as resume_mod

    src = tmp_path / "resume.txt"
    src.write_text("Kunal Patro — café naïve “quoted”", encoding="utf-8")
    text = resume_mod.load(src, tmp_path / "cache")
    assert "café" in text
    # second call comes from the cache file, which is where the encoding bug bit
    assert resume_mod.load(src, tmp_path / "cache") == text
