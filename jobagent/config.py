"""Loading and validating config/ files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = Path(os.environ.get("JOBAGENT_DATA", ROOT / "data"))
RUNS_DIR = Path(os.environ.get("JOBAGENT_RUNS", ROOT / "runs"))


class ConfigError(RuntimeError):
    pass


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(
            f"missing {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}. "
            f"Copy the .example file next to it and fill it in."
        )
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a yaml mapping at the top level")
    return data


@dataclass
class Profile:
    """Everything about you that ends up in a form or a prompt."""

    raw: dict[str, Any]

    @property
    def identity(self) -> dict[str, Any]:
        return self.raw.get("identity", {})

    @property
    def eligibility(self) -> dict[str, Any]:
        return self.raw.get("eligibility", {})

    @property
    def preferences(self) -> dict[str, Any]:
        return self.raw.get("preferences", {})

    @property
    def eeo(self) -> dict[str, Any]:
        return self.raw.get("eeo", {})

    @property
    def answers(self) -> dict[str, str]:
        return self.raw.get("standard_answers", {}) or {}

    @property
    def resume_path(self) -> Path:
        p = self.raw.get("resume_file")
        if not p:
            raise ConfigError("profile.yaml needs resume_file pointing at your resume pdf or docx")
        path = Path(p).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            raise ConfigError(f"resume file not found: {path}")
        return path

    def missing_required(self) -> list[str]:
        required = [
            ("identity", "full_name"),
            ("identity", "email"),
            ("identity", "phone"),
            ("identity", "city"),
            ("identity", "state"),
            ("eligibility", "work_authorization"),
            ("eligibility", "requires_sponsorship"),
            ("preferences", "work_mode"),
        ]
        out = []
        for section, key in required:
            value = getattr(self, section).get(key)
            if value in (None, "", "FILL_ME") or (isinstance(value, str) and "FILL_ME" in value):
                out.append(f"{section}.{key}")
        return out


@dataclass
class Config:
    raw: dict[str, Any]
    profile: Profile
    boards: dict[str, Any] = field(default_factory=dict)

    # --- search ---
    @property
    def max_age_days(self) -> int:
        return int(self.raw.get("search", {}).get("max_age_days", 3))

    @property
    def daily_target(self) -> int:
        return int(self.raw.get("search", {}).get("daily_target", 5))

    @property
    def min_score(self) -> int:
        return int(self.raw.get("search", {}).get("min_score", 70))

    @property
    def titles_include(self) -> list[str]:
        return self.raw.get("search", {}).get("titles_include", [])

    @property
    def titles_exclude(self) -> list[str]:
        return self.raw.get("search", {}).get("titles_exclude", [])

    @property
    def source_budget_seconds(self) -> int:
        """Wall clock ceiling on the fetch stage, so a hung network cannot stall
        the daily run."""
        return int(self.raw.get("search", {}).get("source_budget_seconds", 180))

    @property
    def seniority_allow(self) -> list[str]:
        """Levels worth applying to. Empty means the defaults in filters.py."""
        return self.raw.get("search", {}).get("seniority_allow", [])

    @property
    def locations_allow(self) -> list[str]:
        return self.raw.get("search", {}).get("locations_allow", [])

    @property
    def remote_only(self) -> bool:
        return bool(self.raw.get("search", {}).get("remote_only", False))

    @property
    def companies_block(self) -> list[str]:
        return [c.lower() for c in self.raw.get("search", {}).get("companies_block", [])]

    @property
    def max_per_company_per_day(self) -> int:
        return int(self.raw.get("search", {}).get("max_per_company_per_day", 1))

    # --- llm ---
    @property
    def model(self) -> str:
        return self.raw.get("llm", {}).get("model", "claude-opus-5")

    @property
    def max_scored_per_run(self) -> int:
        return int(self.raw.get("llm", {}).get("max_scored_per_run", 80))

    # --- documents ---
    @property
    def write_cover_letter(self) -> bool:
        return bool(self.raw.get("documents", {}).get("cover_letter", True))

    @property
    def write_tailoring_notes(self) -> bool:
        return bool(self.raw.get("documents", {}).get("tailoring_notes", True))

    @property
    def answer_screening_questions(self) -> bool:
        return bool(self.raw.get("documents", {}).get("screening_answers", True))

    # --- browser ---
    @property
    def cdp_port(self) -> int:
        return int(self.raw.get("browser", {}).get("cdp_port", 9222))

    @property
    def chrome_profile_dir(self) -> Path:
        p = self.raw.get("browser", {}).get("profile_dir", "~/.jobagent/chrome-profile")
        return Path(p).expanduser()

    @property
    def chrome_binary(self) -> str | None:
        return self.raw.get("browser", {}).get("chrome_binary")

    @property
    def never_submit(self) -> bool:
        # Deliberately not configurable to False through anything but an explicit edit here.
        return bool(self.raw.get("browser", {}).get("never_submit", True))

    # --- review server ---
    @property
    def review_port(self) -> int:
        return int(self.raw.get("review", {}).get("port", 8765))


def load(config_path: Path | None = None, profile_path: Path | None = None) -> Config:
    cfg_raw = _read_yaml(config_path or CONFIG_DIR / "config.yaml")
    prof_raw = _read_yaml(profile_path or CONFIG_DIR / "profile.yaml")
    boards_path = CONFIG_DIR / "boards.verified.yaml"
    if not boards_path.exists():
        boards_path = CONFIG_DIR / "boards.yaml"
    boards = _read_yaml(boards_path)
    return Config(raw=cfg_raw, profile=Profile(raw=prof_raw), boards=boards)
