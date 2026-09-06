"""SQLite state: seen jobs, scores, and where each application stands.

Everything the agent knows lives in one file so you can inspect it with any
sqlite browser, and so a rerun never applies to the same posting twice.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Fit, Job, QUEUED, TERMINAL

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    company       TEXT NOT NULL,
    title         TEXT NOT NULL,
    location      TEXT,
    url           TEXT NOT NULL,
    description   TEXT,
    posted_at     TEXT,
    remote        INTEGER,
    ats           TEXT,
    first_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS jobs_posted ON jobs(posted_at);

CREATE TABLE IF NOT EXISTS scores (
    job_id          TEXT PRIMARY KEY REFERENCES jobs(id),
    score           INTEGER NOT NULL,
    verdict         TEXT,
    pitch           TEXT,
    strengths       TEXT,
    gaps            TEXT,
    red_flags       TEXT,
    seniority_match TEXT,
    model           TEXT,
    scored_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    job_id       TEXT PRIMARY KEY REFERENCES jobs(id),
    status       TEXT NOT NULL,
    queued_at    TEXT,
    prepped_at   TEXT,
    prefilled_at TEXT,
    closed_at    TEXT,
    artifacts    TEXT,
    notes        TEXT
);
CREATE INDEX IF NOT EXISTS applications_status ON applications(status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------- jobs ----------

    def known_ids(self) -> set[str]:
        return {r["id"] for r in self.conn.execute("SELECT id FROM jobs")}

    def upsert_jobs(self, jobs: Iterable[Job]) -> tuple[int, int]:
        """Insert jobs we have never seen. Returns (new, duplicate)."""
        new = dup = 0
        for job in jobs:
            row = job.to_row()
            row["first_seen_at"] = _now()
            row["remote"] = None if job.remote is None else int(job.remote)
            try:
                self.conn.execute(
                    """INSERT INTO jobs (id, source, external_id, company, title, location,
                                         url, description, posted_at, remote, ats, first_seen_at)
                       VALUES (:id, :source, :external_id, :company, :title, :location,
                               :url, :description, :posted_at, :remote, :ats, :first_seen_at)""",
                    row,
                )
                new += 1
            except sqlite3.IntegrityError:
                dup += 1
        self.conn.commit()
        return new, dup

    def get_job(self, job_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def unscored_jobs(self, limit: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """SELECT j.* FROM jobs j
                   LEFT JOIN scores s ON s.job_id = j.id
                   WHERE s.job_id IS NULL
                   ORDER BY COALESCE(j.posted_at, j.first_seen_at) DESC
                   LIMIT ?""",
                (limit,),
            )
        )

    # ---------- scores ----------

    def save_fit(self, fit: Fit) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO scores
               (job_id, score, verdict, pitch, strengths, gaps, red_flags,
                seniority_match, model, scored_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fit.job_id, fit.score, fit.verdict, fit.pitch,
                json.dumps(fit.strengths), json.dumps(fit.gaps), json.dumps(fit.red_flags),
                fit.seniority_match, fit.model, fit.scored_at.isoformat(),
            ),
        )
        self.conn.commit()

    # ---------- application queue ----------

    def candidates(self, min_score: int) -> list[sqlite3.Row]:
        """Scored, above threshold, never queued or applied before."""
        return list(
            self.conn.execute(
                """SELECT j.*, s.score, s.verdict, s.pitch, s.strengths, s.gaps,
                          s.red_flags, s.seniority_match
                   FROM jobs j
                   JOIN scores s ON s.job_id = j.id
                   LEFT JOIN applications a ON a.job_id = j.id
                   WHERE a.job_id IS NULL AND s.score >= ?
                   ORDER BY s.score DESC, COALESCE(j.posted_at, j.first_seen_at) DESC""",
                (min_score,),
            )
        )

    def enqueue(self, job_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO applications (job_id, status, queued_at) VALUES (?, ?, ?)",
            (job_id, QUEUED, _now()),
        )
        self.conn.commit()

    def set_status(self, job_id: str, status: str, **fields: Any) -> None:
        cols, vals = ["status = ?"], [status]
        for key, value in fields.items():
            cols.append(f"{key} = ?")
            vals.append(json.dumps(value) if isinstance(value, (dict, list)) else value)
        if status in TERMINAL:
            cols.append("closed_at = ?")
            vals.append(_now())
        vals.append(job_id)
        self.conn.execute(f"UPDATE applications SET {', '.join(cols)} WHERE job_id = ?", vals)
        self.conn.commit()

    def queue(self, statuses: Iterable[str] | None = None) -> list[sqlite3.Row]:
        statuses = list(statuses) if statuses else None
        sql = """SELECT j.*, s.score, s.verdict, s.pitch, s.strengths, s.gaps,
                        s.red_flags, s.seniority_match,
                        a.status, a.queued_at, a.prepped_at, a.prefilled_at,
                        a.artifacts, a.notes
                 FROM applications a
                 JOIN jobs j ON j.id = a.job_id
                 LEFT JOIN scores s ON s.job_id = a.job_id"""
        params: list[Any] = []
        if statuses:
            sql += f" WHERE a.status IN ({','.join('?' * len(statuses))})"
            params = statuses
        sql += " ORDER BY s.score DESC, a.queued_at DESC"
        return list(self.conn.execute(sql, params))

    def applied_companies_today(self) -> dict[str, int]:
        rows = self.conn.execute(
            """SELECT j.company, COUNT(*) n FROM applications a
               JOIN jobs j ON j.id = a.job_id
               WHERE date(a.queued_at) = date('now') GROUP BY j.company"""
        )
        return {r["company"].lower(): r["n"] for r in rows}

    def counts(self) -> dict[str, int]:
        out = {
            "jobs": self.conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"],
            "scored": self.conn.execute("SELECT COUNT(*) c FROM scores").fetchone()["c"],
        }
        for row in self.conn.execute("SELECT status, COUNT(*) c FROM applications GROUP BY status"):
            out[row["status"]] = row["c"]
        return out
