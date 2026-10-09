"""Loads the source registry from config/sites.yaml."""

from pathlib import Path

import yaml
from pydantic import BaseModel

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "sites.yaml"


class SiteConfig(BaseModel):
    name: str
    start_url: str
    allowed_hosts: list[str]
    hint: str | None = None
    requires_login: bool = False
    max_list_pages: int = 10  # safety cap on "load more" steps per run
    first_run_days: int = 3  # with an empty database, collect postings from this many days back


def load_sites(path: Path = DEFAULT_PATH) -> list[SiteConfig]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [SiteConfig(**item) for item in data["sites"]]
