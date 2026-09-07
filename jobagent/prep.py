"""Generate the per job material you would otherwise write by hand.

Nothing here rewrites your resume file. Your master resume pdf is what gets
uploaded, because a machine rebuilt pdf is the fastest way to look worse than you
are. What we generate is the free text a form actually asks for, plus a short
note on what to emphasise if you decide to tweak the resume yourself.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .config import Config
from .scoring import preferences_block

log = logging.getLogger(__name__)

SYSTEM_TEMPLATE = """\
You write job application material for one candidate. You write the way a
competent person writes, not the way a cover letter generator writes.

Rules, all of them non negotiable:
* Never invent a job, employer, metric, date, tool or credential. Everything you
  claim must be traceable to the resume below. If the posting wants something the
  candidate does not have, do not paper over it, just leave it out.
* No filler openings. Never write "I am excited to apply", "I am passionate about",
  "As a seasoned professional", or anything of that shape.
* Concrete over abstract. One real shipped thing beats three adjectives.
* Match the register of a normal work email. Short sentences. No em dashes.
* If you are unsure about a fact, write it in a way that stays true.

CANDIDATE RESUME
{resume}

CANDIDATE CONSTRAINTS AND PREFERENCES
{preferences}
"""

USER_TEMPLATE = """\
Write the application material for this role.

Company: {company}
Title: {title}
Location: {location}

JOB DESCRIPTION
{description}

WHY WE THINK THIS IS A FIT
{pitch}
Strengths we identified: {strengths}
Gaps we identified: {gaps}

Answer each of these questions as the candidate, in first person:
{questions}

Cover letter: {cover_letter_instruction}
"""

DEFAULT_QUESTIONS = [
    "Why do you want to work at this company?",
    "Why are you a good fit for this specific role?",
    "Describe a product you shipped and the outcome it drove.",
]


class QA(BaseModel):
    question: str
    answer: str = Field(description="First person, 60 to 120 words, specific, no filler openings.")


class Material(BaseModel):
    cover_letter: str = Field(
        default="",
        description="Under 200 words. No address block, no Dear Hiring Manager, "
        "no signature. Three short paragraphs at most. Empty string if not requested.",
    )
    answers: list[QA] = Field(default_factory=list)
    tailoring_notes: str = Field(
        default="",
        description="Markdown. What to emphasise or reorder on the resume for this "
        "specific posting, and which exact keywords from the posting are missing "
        "from the resume but are things the candidate has genuinely done.",
    )
    resume_keywords_missing: list[str] = Field(
        default_factory=list,
        description="Terms the posting screens on that do not appear in the resume.",
    )


def slugify(text: str, limit: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:limit] or "job"


def artifact_dir(runs_dir: Path, job_row) -> Path:
    day = (job_row["queued_at"] or "")[:10] or "unscheduled"
    name = f"{slugify(job_row['company'], 30)}--{slugify(job_row['title'], 50)}--{job_row['id'][:6]}"
    return runs_dir / day / name


class Prepper:
    def __init__(self, cfg: Config, resume_text: str, client: Any = None):
        self.cfg = cfg
        self.system = SYSTEM_TEMPLATE.format(
            resume=resume_text, preferences=preferences_block(cfg)
        )
        self.questions = cfg.raw.get("documents", {}).get("questions") or DEFAULT_QUESTIONS
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client

    def generate(self, job_row) -> Material | None:
        strengths = json.loads(job_row["strengths"] or "[]")
        gaps = json.loads(job_row["gaps"] or "[]")
        questions = self.questions if self.cfg.answer_screening_questions else []
        cover_instruction = (
            "write one" if self.cfg.write_cover_letter else "not requested, return an empty string"
        )
        prompt = USER_TEMPLATE.format(
            company=job_row["company"],
            title=job_row["title"],
            location=job_row["location"] or "not stated",
            description=(job_row["description"] or "")[:12000],
            pitch=job_row["pitch"] or "",
            strengths="; ".join(strengths) or "none listed",
            gaps="; ".join(gaps) or "none listed",
            questions="\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions)) or "none",
            cover_letter_instruction=cover_instruction,
        )
        try:
            response = self.client.messages.parse(
                model=self.cfg.model,
                max_tokens=8000,
                system=[
                    {
                        "type": "text",
                        "text": self.system,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
                messages=[{"role": "user", "content": prompt}],
                output_format=Material,
            )
        except Exception as exc:
            log.warning("prep failed for %s: %s", job_row["id"], exc)
            return None
        if getattr(response, "stop_reason", None) == "refusal":
            log.warning("model declined to write material for %s", job_row["id"])
            return None
        return getattr(response, "parsed_output", None)


def write_artifacts(directory: Path, job_row, material: Material) -> dict[str, str]:
    """Write everything to disk and return a map of label to path."""
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    summary = [
        f"# {job_row['title']} at {job_row['company']}",
        "",
        f"Location: {job_row['location'] or 'not stated'}",
        f"Posted: {job_row['posted_at'] or 'unknown'}",
        f"Fit score: {job_row['score']} ({job_row['verdict']})",
        f"Apply: {job_row['url']}",
        "",
        "## Why this one",
        job_row["pitch"] or "",
        "",
        "## Strengths",
        *(f"* {s}" for s in json.loads(job_row["strengths"] or "[]")),
        "",
        "## Gaps",
        *(f"* {g}" for g in json.loads(job_row["gaps"] or "[]")),
    ]
    red_flags = json.loads(job_row["red_flags"] or "[]")
    if red_flags:
        summary += ["", "## Red flags", *(f"* {r}" for r in red_flags)]
    (directory / "brief.md").write_text("\n".join(summary), encoding="utf-8")
    written["brief"] = str(directory / "brief.md")

    if material.cover_letter.strip():
        (directory / "cover_letter.txt").write_text(material.cover_letter.strip() + "\n", encoding="utf-8")
        written["cover_letter"] = str(directory / "cover_letter.txt")

    if material.answers:
        payload = [{"question": a.question, "answer": a.answer} for a in material.answers]
        (directory / "answers.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        readable = "\n\n".join(f"Q: {a.question}\n\n{a.answer}" for a in material.answers)
        (directory / "answers.md").write_text(readable + "\n", encoding="utf-8")
        written["answers"] = str(directory / "answers.json")

    if material.tailoring_notes.strip():
        notes = material.tailoring_notes.strip()
        if material.resume_keywords_missing:
            notes += "\n\n## Keywords in the posting, missing from the resume\n"
            notes += "\n".join(f"* {k}" for k in material.resume_keywords_missing)
        (directory / "tailoring_notes.md").write_text(notes + "\n", encoding="utf-8")
        written["tailoring_notes"] = str(directory / "tailoring_notes.md")

    return written
