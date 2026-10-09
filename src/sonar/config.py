"""Loads the mode flags and run settings from config/settings.yaml."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = ROOT / "config" / "settings.yaml"


class Settings(BaseModel):
    navigator: Literal["dom", "vision"] = "dom"
    matcher: Literal["keywords", "jev"] = "keywords"
    database_url: str = "sqlite:///data/sonar.db"
    keywords_file: str = "config/keywords.yaml"
    headless: bool = False
    cdp_port: int = 9333
    report_dir: str = "data/reports"

    @property
    def report_path(self) -> Path:
        return ROOT / self.report_dir

    @property
    def keywords_path(self) -> Path:
        return ROOT / self.keywords_file


def load_settings(path: Path = DEFAULT_PATH) -> Settings:
    return Settings(**yaml.safe_load(path.read_text(encoding="utf-8")))
