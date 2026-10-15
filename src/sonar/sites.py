"""Loads the source registry from config/sites.yaml."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

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
    # Exact URL paths the browser guards let a POST through, because the site loads list rows that way (sort, page).
    # The developer adds a path only after checking that it just reads. Never a pattern.
    read_post_paths: list[str] = []
    login_url: str | None = None  # the page with the login form; needed when requires_login is true
    max_list_pages: int = 10  # safety cap on list steps (load more / next page) per run
    first_run_days: int = 3  # with an empty database, collect postings from this many days back
    # Pause before each posting is opened (after the first), with +-30% random jitter, for sites that start refusing
    # (403) a browser that opens pages faster than a person reads them. 0 = no pause.
    open_delay_seconds: float = Field(default=0, ge=0, le=120)
    # True: every matched posting of this site carries the note 'please review the attached documents' in the email,
    # for sites whose documents cannot be seen without an account step (SAP Ariba prerequisites).
    attachment_note: bool = False
    # A plain label for the login this entry uses ("Ariba account 1"), shown next to each of its matches so a tester
    # knows which account can open the posting. Never a password. One entry per account: each gets its own
    # browser profile, watermark and failure handling, and credentials SONAR_<NAME>_USERNAME / _PASSWORD.
    account: str | None = None

    @field_validator("read_post_paths")
    @classmethod
    def _exact_paths(cls, paths: list[str]) -> list[str]:
        for path in paths:
            if not path.startswith("/") or any(c in path for c in "*?#") or "://" in path:
                raise ValueError(f"read_post_paths needs exact paths like /app/list.si, not {path!r}")
        return paths

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
        if data.get("requires_login") and not data.get("login_url"):
            raise ValueError(f"site '{data.get('name')}' has requires_login but no login_url")
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
            "\nHints from the operator. If a hint lists steps, they worked on a past run: follow them in order, "
            "in as few tool calls as you can, instead of searching for the controls yourself. They may be out of date: "
            "if a step fails or the page disagrees, trust the page and find the control yourself.\n"
            + "\n".join(lines)
        )


def load_sites(path: Path = DEFAULT_PATH) -> list[SiteConfig]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [SiteConfig(**item) for item in data["sites"]]


def site_accounts(path: Path = DEFAULT_PATH) -> dict[str, str]:
    """Site name to account label, for the sites that set one."""
    return {s.name: s.account for s in load_sites(path) if s.account}


def note_sites(path: Path = DEFAULT_PATH) -> frozenset[str]:
    """Names of the sites whose matched postings always get the 'review the attached documents' note."""
    return frozenset(s.name for s in load_sites(path) if s.attachment_note)
