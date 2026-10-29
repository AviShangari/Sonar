"""Loads the mode flags and run settings from config/settings.yaml."""

import os
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, field_validator, model_validator

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = ROOT / "config" / "settings.yaml"


class Settings(BaseModel):
    navigation_model: str = "openai/gpt-6.1-sol"  # the browsing agent's model (OpenRouter name; see llm.py)
    navigation_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = "medium"
    summary_model: str = "anthropic/claude-sonnet-5.5"  # the summary writer's model
    assistant_model: str = "openai/gpt-6.1-sol"  # the terminal assistant that answers questions about stored reports
    navigator: Literal["dom", "vision", "hermes"] = "hermes"
    matcher: Literal["keywords", "jev"] = "jev"
    database_url: str = "sqlite:///data/sonar.db"
    keywords_file: str = "config/keywords.yaml"
    company_file: str = "config/company.yaml"  # what the client does: the Jev question text (matcher: jev only)
    jev_model: str = "typesafe/jev-1.13"  # pinned on purpose: thresholds only mean something for one model version
    jev_match_threshold: float = 0.6  # probability at or above this: a match
    jev_review_threshold: float = 0.3  # from here up to the match threshold: a match flagged "needs review"; below: not a match
    jev_max_chars: int = 12000  # posting text sent to Jev is cut to this length
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
    max_run_usd: float | None = None  # dollars per run, from the OpenRouter key's spend
    max_call_usd: float | None = None  # dollars per agent call, summed from the cost OpenRouter reports in each reply
    email: bool = True  # on by default; never turn off without the developer's say-so (AGENTS.md "Email")
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

    @model_validator(mode="after")
    def _jev_thresholds_in_order(self) -> "Settings":
        if not 0 <= self.jev_review_threshold < self.jev_match_threshold <= 1:
            raise ValueError("jev thresholds must satisfy 0 <= jev_review_threshold < jev_match_threshold <= 1")
        return self

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

    @property
    def company_path(self) -> Path:
        return ROOT / self.company_file


HERMES_PATH = ROOT / "config" / "hermes.yaml"


class HermesSettings(BaseModel):
    """How Sonar runs the Hermes Agent (config/hermes.yaml). Used only when `navigator: hermes`."""

    home: str  # Hermes's own folder: config, saved notes (skills), memory, logs. Keep the path SHORT (a long one breaks a Windows DLL)
    install_dir: str = "%LOCALAPPDATA%/hermes/hermes-agent"  # where `install.ps1` put Hermes's code
    executable: str = "%LOCALAPPDATA%/hermes/bin/hermes.exe"
    model: str = "openai/gpt-6.1-sol"  # pinned: the server otherwise picks its own default; any other model answering stops the run
    provider: str = "openrouter"
    api_port: int = 8642  # the local-only server Sonar talks to
    max_turns: int = 50  # model calls Hermes may make for one task
    task_seconds: int = 480  # one task (a whole list page, or a "load more" step)
    snapshot_threshold: int = 90000
    learn: bool = True  # Hermes may save and update its own notes (skills); the guard allows create and patch only

    @property
    def home_path(self) -> Path:
        return Path(os.path.expandvars(self.home))

    @property
    def install_path(self) -> Path:
        return Path(os.path.expandvars(self.install_dir))

    @property
    def executable_path(self) -> Path:
        return Path(os.path.expandvars(self.executable))


def load_hermes_settings(path: Path = HERMES_PATH) -> HermesSettings:
    return HermesSettings(**yaml.safe_load(path.read_text(encoding="utf-8")))


def load_settings(path: Path = DEFAULT_PATH) -> Settings:
    values = yaml.safe_load(path.read_text(encoding="utf-8"))
    if os.environ.get("SONAR_HEADLESS", "").lower() in ("1", "true", "yes"):
        values["headless"] = True  # the Docker image sets this: a container has no screen, whatever settings.yaml says
    return Settings(**values)
