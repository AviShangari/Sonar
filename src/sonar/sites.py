"""Loads the source registry from config/sites.yaml."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, model_validator

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "sites.yaml"


class SiteConfig(BaseModel):
    name: str
    start_url: str
    # In sites.yaml each entry is a host name, or {host: ..., hint: ...} to give the agent a tip about that host.
    # Loaded into a plain list of host names plus `host_hints`.
    allowed_hosts: list[str]
    host_hints: dict[str, str] = {}
    hint: str | None = None
    requires_login: bool = False
    max_list_pages: int = 10  # safety cap on list steps (load more / next page) per run
    first_run_days: int = 3  # with an empty database, collect postings from this many days back

    @model_validator(mode="before")
    @classmethod
    def _split_host_hints(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        hosts: list[str] = []
        hints: dict[str, str] = dict(data.get("host_hints") or {})
        for item in data.get("allowed_hosts") or []:
            if isinstance(item, dict):
                if not item.get("host"):
                    raise ValueError(f"allowed_hosts entry without a host: {item}")
                hosts.append(item["host"])
                if item.get("hint"):
                    hints[item["host"]] = item["hint"]
            else:
                hosts.append(item)
        if unknown := set(hints) - set(hosts):
            raise ValueError(f"hints for hosts that are not allowed: {sorted(unknown)}")
        return {**data, "allowed_hosts": hosts, "host_hints": hints}

    def hints_text(self) -> str:
        """The operator's hints for the agent, or "" when there are none. Hints can go stale when a
        site is redesigned, so the agent is told to trust the page over them."""
        lines = [f"- About this site: {self.hint}"] if self.hint else []
        lines += [f"- About {host}: {tip}" for host, tip in self.host_hints.items()]
        if not lines:
            return ""
        return (
            "\nHints from the operator (they may be out of date; if the page disagrees, trust the page):\n"
            + "\n".join(lines)
        )


def load_sites(path: Path = DEFAULT_PATH) -> list[SiteConfig]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [SiteConfig(**item) for item in data["sites"]]
