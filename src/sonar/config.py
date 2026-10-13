"""Loads the mode flags and run settings from config/settings.yaml."""

from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, field_validator

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = ROOT / "config" / "settings.yaml"


class Settings(BaseModel):
    navigator: Literal["dom", "vision"] = "dom"
    matcher: Literal["keywords", "jev"] = "keywords"
    database_url: str = "sqlite:///data/sonar.db"
    keywords_file: str = "config/keywords.yaml"
    timezone: str = "America/Toronto"  # the business's time zone: report times and "today" for date windows
    headless: bool = False
    cdp_port: int = 9333
    report_dir: str = "data/reports"
    lock_dir: str = "data"  # holds the run lock file
    screenshot_dir: str = "data/screenshots"  # failure screenshots (gitignored, for people only)
    summaries: bool = True
    summary_workers: int = 4
    email: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""  # blank: log in with email_from
    email_from: str = ""
    email_to: str = ""

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, name: str) -> str:
        try:
            ZoneInfo(name)  # fails at startup, not in the middle of a run, if the name is wrong
        except (KeyError, ValueError) as exc:
            raise ValueError(f"unknown time zone '{name}' (use a name like America/Toronto)") from exc
        return name

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def today(self) -> date:
        """Today's date in the business time zone (the machine's own clock may be UTC, e.g. on a VM)."""
        return datetime.now(self.tz).date()

    @property
    def report_path(self) -> Path:
        return ROOT / self.report_dir

    @property
    def lock_path(self) -> Path:
        return ROOT / self.lock_dir

    @property
    def screenshot_path(self) -> Path:
        return ROOT / self.screenshot_dir

    @property
    def keywords_path(self) -> Path:
        return ROOT / self.keywords_file


def load_settings(path: Path = DEFAULT_PATH) -> Settings:
    return Settings(**yaml.safe_load(path.read_text(encoding="utf-8")))
