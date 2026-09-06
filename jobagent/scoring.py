"""Score a posting against your resume with Claude.

One system prompt carries your resume and preferences and stays byte identical
across every job in a run, so it hits the prompt cache after the first call. Only
the job description varies, and it goes in the user turn.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from .config import Config
from .models import Fit

log = logging.getLogger(__name__)

SYSTEM_TEMPLATE = """\
You are screening product management job postings on behalf of one candidate.
You are not a cheerleader. Your job is to protect the candidate's time: they will
apply to at most {daily_target} roles a day, so a posting has to earn its place.

Score honestly. A posting that requires 10 years when they have 4, or is in a
domain they have never touched, is a weak fit even if the title matches. Most
postings should land between 40 and 70. Reserve 85 and above for roles where a
recruiter reading their resume would obviously want to talk to them.

CANDIDATE RESUME
{resume}

CANDIDATE CONSTRAINTS AND PREFERENCES
{preferences}

Scoring guide
90-100  near perfect. Level, domain and skills all line up, they would be a top applicant.
75-89   strong. Clear overlap, any gaps are learnable and not gating requirements.
60-74   stretch. Real overlap but a gating requirement is missing, or the level is off by one.
40-59   weak. Some transferable skill, but a recruiter would screen them out.
0-39    wrong job. Different discipline, wrong level, or a hard blocker.

Hard blockers that cap the score at 35 no matter what else fits:
* requires a security clearance the candidate does not have
* requires work authorization or sponsorship terms the candidate cannot meet
* the location is not somewhere the candidate can work
* it is not actually a product management role
"""

USER_TEMPLATE = """\
Assess this posting.

Company: {company}
Title: {title}
Location: {location}
Posted: {posted}

JOB DESCRIPTION
{description}
"""


class FitAssessment(BaseModel):
    """Structured verdict the model must return."""

    score: int = Field(ge=0, le=100, description="Overall fit, using the scoring guide.")
    verdict: str = Field(description="One of: strong, good, stretch, weak.")
    seniority_match: str = Field(description="One of: under, match, over.")
    pitch: str = Field(
        description="One sentence the candidate could say about why they fit this specific role. "
        "Concrete, drawn from their resume, no adjectives like passionate or excited."
    )
    strengths: list[str] = Field(
        default_factory=list,
        description="Up to 4 specific overlaps between the resume and the requirements.",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="Up to 4 requirements the candidate does not clearly meet.",
    )
    red_flags: list[str] = Field(
        default_factory=list,
        description="Blockers or warning signs: clearance, sponsorship, wrong location, "
        "unpaid, disguised sales role, obvious ghost posting.",
    )


def preferences_block(cfg: Config) -> str:
    profile = cfg.profile
    prefs = profile.preferences
    elig = profile.eligibility
    lines = [
        f"Work authorization: {elig.get('work_authorization', 'not stated')}",
        f"Needs visa sponsorship: {elig.get('requires_sponsorship', 'not stated')}",
        f"Based in: {profile.identity.get('location', 'not stated')}",
        f"Open to relocation: {prefs.get('relocation', 'not stated')}",
        f"Acceptable locations: {', '.join(cfg.locations_allow) or 'any US location'}",
        f"Remote preference: {prefs.get('work_mode', 'not stated')}",
        f"Target levels: {', '.join(prefs.get('target_levels', []) ) or 'not stated'}",
        f"Years of product management experience: {prefs.get('years_pm', 'not stated')}",
        f"Total years working: {prefs.get('years_total', 'not stated')}",
        f"Strong domains: {', '.join(prefs.get('domains_strong', [])) or 'not stated'}",
        f"Interested but less experienced in: {', '.join(prefs.get('domains_interested', [])) or 'none'}",
        f"Minimum base salary (USD): {prefs.get('min_base_salary', 'not stated')}",
        f"Company sizes wanted: {', '.join(prefs.get('company_sizes', [])) or 'any'}",
        f"Will not work on: {', '.join(prefs.get('dealbreakers', [])) or 'nothing stated'}",
    ]
    return "\n".join(lines)


def build_system(cfg: Config, resume_text: str) -> str:
    return SYSTEM_TEMPLATE.format(
        daily_target=cfg.daily_target,
        resume=resume_text,
        preferences=preferences_block(cfg),
    )


def _truncate(text: str, limit: int = 12000) -> str:
    """Job descriptions are occasionally enormous. Keep the top, it holds the
    requirements; the tail is usually benefits and legal boilerplate."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[description truncated]"


class Scorer:
    def __init__(self, cfg: Config, resume_text: str, client: Any = None):
        self.cfg = cfg
        self.system = build_system(cfg, resume_text)
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client

    def score(self, job_row) -> Fit | None:
        prompt = USER_TEMPLATE.format(
            company=job_row["company"],
            title=job_row["title"],
            location=job_row["location"] or "not stated",
            posted=job_row["posted_at"] or "unknown",
            description=_truncate(job_row["description"] or ""),
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
                output_format=FitAssessment,
            )
        except Exception as exc:  # network, rate limit, validation
            log.warning("scoring failed for %s at %s: %s", job_row["title"], job_row["company"], exc)
            return None

        if getattr(response, "stop_reason", None) == "refusal":
            log.warning("model declined to score %s (%s)", job_row["id"], response.stop_details)
            return None

        parsed: FitAssessment | None = getattr(response, "parsed_output", None)
        if parsed is None:
            log.warning("no structured output for %s", job_row["id"])
            return None

        return Fit(
            job_id=job_row["id"],
            score=parsed.score,
            verdict=parsed.verdict,
            pitch=parsed.pitch,
            strengths=parsed.strengths,
            gaps=parsed.gaps,
            red_flags=parsed.red_flags,
            seniority_match=parsed.seniority_match,
            model=self.cfg.model,
        )


def heuristic_score(job_row, resume_text: str) -> Fit:
    """Keyword overlap fallback for when there is no API key.

    Deliberately crude. It exists so the pipeline is testable end to end offline,
    not so you can rely on it.
    """
    import re
    from collections import Counter

    stop = set("""a an the and or of to in for with on at by from as is are be this that we you
    our your their will can our who what how role team product work working experience years""".split())
    words = lambda s: [w for w in re.findall(r"[a-z][a-z+#.]{2,}", s.lower()) if w not in stop]
    resume_terms = Counter(words(resume_text))
    job_terms = set(words(job_row["description"] or ""))
    if not job_terms:
        overlap = 0.0
    else:
        hits = sum(1 for t in job_terms if resume_terms.get(t))
        overlap = hits / len(job_terms)
    score = int(min(95, max(5, overlap * 220)))
    return Fit(
        job_id=job_row["id"],
        score=score,
        verdict="heuristic",
        pitch="Scored offline by keyword overlap. Run with an API key for a real assessment.",
        strengths=[],
        gaps=[],
        red_flags=["scored without the model"],
        seniority_match="unknown",
        model="heuristic",
    )
