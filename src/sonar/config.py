"""Loads the mode flags and run settings from config/settings.yaml."""

import os
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, field_validator

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = ROOT / "config" / "settings.yaml"


class Settings(BaseModel):
    auth: Literal["subscription", "openrouter"] = "subscription"  # see llm.py
    navigation_model: str = "openai/gpt-6.1-sol"  # used only with auth: openrouter
    navigation_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = "medium"
    summary_model: str = "anthropic/claude-sonnet-5.5"  # used only with auth: openrouter
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
    route_notes_dir: str = "data/route-notes"  # how the agent reached each site's list (gitignored, for people only)
    summaries: bool = True
    summary_workers: int = 4
    # Cost caps per run (see budget.py). Hitting one ends the run as "partial". None = no limit.
    max_agent_calls: int | None = 80  # all agent calls in a run: list-page reads plus summaries
    max_new_per_site: int | None = 100  # new postings stored per site
    max_summaries: int | None = 40  # summaries written per run
    max_run_usd: float | None = None  # dollars per run, from the OpenRouter key's spend (auth: openrouter only)
    max_call_usd: float | None = None  # dollars per agent call, the SDK's own figure (see decision log before trusting it)
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
    def route_notes_path(self) -> Path:
        return ROOT / self.route_notes_dir

    @property
    def keywords_path(self) -> Path:
        return ROOT / self.keywords_file


def load_settings(path: Path = DEFAULT_PATH) -> Settings:
    values = yaml.safe_load(path.read_text(encoding="utf-8"))
    if os.environ.get("SONAR_HEADLESS", "").lower() in ("1", "true", "yes"):
        values["headless"] = True  # the Docker image sets this: a container has no screen, whatever settings.yaml says
    return Settings(**values)
