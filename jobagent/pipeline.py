"""The daily run, stage by stage.

    source  -> pull postings from every configured board
    score   -> ask Claude how well each one fits you
    queue   -> pick today's shortlist
    prep    -> write the cover letter and screening answers
    prefill -> open each form in your Chrome and fill it, then stop

`run` does all of them in order. Each is also callable on its own.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import datetime, timezone
from pathlib import Path

from . import resume as resume_mod
from .config import Config, DATA_DIR, RUNS_DIR, CONFIG_DIR
from .filters import TitleFilter, prefilter
from .models import PREFILLED, PREPPED, QUEUED, FAILED
from .prep import Prepper, artifact_dir, write_artifacts
from .scoring import Scorer, heuristic_score
from .sources import ashby, greenhouse, lever, manual, readers, smartrecruiters
from .store import Store

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# A red flag only blocks the queue when it says the door is shut. "Candidate will
# need sponsorship" is a fact about the candidate, not a rejection, and matching
# the bare word "sponsor" would empty the queue for anyone who needs one.
BLOCKING_FLAG = re.compile(
    r"(security clearance|ts/sci|polygraph"
    r"|(?:no|not|cannot|can not|unable to|will not|does not|do not)\s+\w*\s*sponsor"
    r"|sponsorship (?:is )?not (?:available|offered|provided)"
    r"|without sponsorship"
    r"|citizen(?:ship)? (?:only|required)"
    r"|must be a u\.?s\.? (?:citizen|person))",
    re.I,
)


def get_store(cfg: Config) -> Store:
    return Store(DATA_DIR / "jobagent.db")


def resume_text(cfg: Config) -> str:
    return resume_mod.load(cfg.profile.resume_path, DATA_DIR)


# --------------------------------------------------------------------------- source

def stage_source(cfg: Config, store: Store) -> dict:
    title_filter = TitleFilter(cfg.titles_include or None, cfg.titles_exclude or None)
    boards = cfg.boards or {}
    raw_jobs = []

    # One board is one http call and almost all of the wall clock is waiting, so
    # fetch them concurrently. A board that is down must never hold up the run.
    fetchers = {
        "greenhouse": greenhouse.fetch_company,
        "lever": lever.fetch_company,
        "ashby": ashby.fetch_company,
        # SmartRecruiters needs a detail call per posting, so it gets the title
        # filter up front and only pays for postings that could matter.
        "smartrecruiters": lambda token: smartrecruiters.fetch_company(
            token, title_filter=title_filter
        ),
    }
    work = [(ats, token) for ats in fetchers for token in (boards.get(ats) or [])]
    timed_out = 0

    if work:
        log.info("fetching %d company boards", len(work))
        # Not a `with` block on purpose: its shutdown waits for every worker, which
        # would hand the wall clock back to the slowest board and defeat the budget.
        # The timeout goes on as_completed, which is what actually blocks. Stragglers
        # are bounded by the http timeout in sources/base.py and their results are
        # simply discarded.
        pool = ThreadPoolExecutor(max_workers=12)
        try:
            futures = {
                pool.submit(lambda a, t: list(fetchers[a](t)), ats, token): (ats, token)
                for ats, token in work
            }
            try:
                for future in as_completed(futures, timeout=cfg.source_budget_seconds):
                    ats, token = futures[future]
                    try:
                        raw_jobs.extend(future.result())
                    except Exception as exc:
                        log.debug("%s/%s failed: %s", ats, token, exc)
            except TimeoutError:
                pass                      # out of budget, keep whatever arrived
            timed_out = sum(1 for f in futures if not f.done())
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if timed_out:
            log.warning("%d of %d boards did not answer inside the %ds budget",
                        timed_out, len(work), cfg.source_budget_seconds)

    manual_file = CONFIG_DIR / "manual_urls.txt"
    if manual_file.exists():
        chain = readers.build_chain(cfg.readers, cfg.reader_command)
        raw_jobs.extend(manual.fetch(manual_file, chain))

    keep, reasons = [], Counter()
    for job, dropped in prefilter(raw_jobs, cfg, title_filter):
        if dropped:
            reasons[dropped] += 1
        else:
            keep.append(job)

    new, dup = store.upsert_jobs(keep)
    return {
        "boards": len(work),
        "boards_timed_out": timed_out,
        "fetched": len(raw_jobs),
        "passed_filters": len(keep),
        "new": new,
        "already_known": dup,
        "dropped": dict(reasons),
    }


# --------------------------------------------------------------------------- score

def stage_score(cfg: Config, store: Store, offline: bool = False) -> dict:
    pending = store.unscored_jobs(cfg.max_scored_per_run)
    if not pending:
        return {"scored": 0}
    text = resume_text(cfg)
    scored = failed = 0
    if offline:
        for row in pending:
            store.save_fit(heuristic_score(row, text))
            scored += 1
        return {"scored": scored, "mode": "heuristic"}

    scorer = Scorer(cfg, text)
    for row in pending:
        fit = scorer.score(row)
        if fit is None:
            failed += 1
            continue
        store.save_fit(fit)
        scored += 1
        log.info("%3d  %s at %s", fit.score, row["title"], row["company"])
    return {"scored": scored, "failed": failed, "model": cfg.model}


# --------------------------------------------------------------------------- queue

def stage_queue(cfg: Config, store: Store) -> dict:
    candidates = store.candidates(cfg.min_score)
    per_company = store.applied_companies_today()
    picked = []
    for row in candidates:
        if len(picked) >= cfg.daily_target:
            break
        company = row["company"].lower()
        if per_company.get(company, 0) >= cfg.max_per_company_per_day:
            continue
        red_flags = json.loads(row["red_flags"] or "[]")
        if any(BLOCKING_FLAG.search(f) for f in red_flags):
            continue
        store.enqueue(row["id"])
        per_company[company] = per_company.get(company, 0) + 1
        picked.append({"id": row["id"], "score": row["score"],
                       "title": row["title"], "company": row["company"]})
    return {
        "eligible": len(candidates),
        "queued": len(picked),
        "target": cfg.daily_target,
        "picks": picked,
    }


# --------------------------------------------------------------------------- prep

def stage_prep(cfg: Config, store: Store) -> dict:
    rows = store.queue([QUEUED])
    if not rows:
        return {"prepped": 0}
    prepper = Prepper(cfg, resume_text(cfg))
    done = failed = 0
    for row in rows:
        material = prepper.generate(row)
        if material is None:
            failed += 1
            continue
        directory = artifact_dir(RUNS_DIR, row)
        artifacts = write_artifacts(directory, row, material)
        store.set_status(row["id"], PREPPED, artifacts=json.dumps(artifacts),
                         prepped_at=_now())
        done += 1
        log.info("prepped %s at %s -> %s", row["title"], row["company"], directory)
    return {"prepped": done, "failed": failed}


# --------------------------------------------------------------------------- prefill

def stage_prefill(cfg: Config, store: Store, job_ids: list[str] | None = None) -> dict:
    from .fill.browser import Browser
    from .fill.filler import Filler

    rows = [r for r in store.queue([PREPPED, QUEUED]) if not job_ids or r["id"] in job_ids]
    if not rows:
        return {"prefilled": 0}

    filler = Filler(cfg.profile, cfg.profile.resume_path)
    results = []
    with Browser(cfg.cdp_port, cfg.chrome_profile_dir, cfg.chrome_binary) as browser:
        for row in rows:
            artifacts = json.loads(row["artifacts"] or "{}")
            log.info("opening %s at %s", row["title"], row["company"])
            try:
                page = browser.new_page(row["url"])
            except Exception as exc:
                store.set_status(row["id"], FAILED, notes=f"could not open: {exc}")
                results.append({"id": row["id"], "error": str(exc)})
                continue
            report = filler.fill_page(page, row, artifacts)
            directory = Path(artifacts.get("brief", "")).parent if artifacts.get("brief") else \
                artifact_dir(RUNS_DIR, row)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "fill_report.json").write_text(
                json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            status = FAILED if report.error else PREFILLED
            store.set_status(row["id"], status, notes=report.summary(),
                             prefilled_at=_now())
            results.append({"id": row["id"], "company": row["company"],
                            "title": row["title"], "summary": report.summary()})
            log.info("  %s", report.summary())
    return {"prefilled": len(results), "results": results}


# --------------------------------------------------------------------------- all

def run_all(cfg: Config, store: Store, offline: bool = False, prefill: bool = True) -> dict:
    out = {"source": stage_source(cfg, store)}
    out["score"] = stage_score(cfg, store, offline=offline)
    out["queue"] = stage_queue(cfg, store)
    if not offline:
        out["prep"] = stage_prep(cfg, store)
    if prefill:
        out["prefill"] = stage_prefill(cfg, store)
    return out
