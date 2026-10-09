"""The navigator interface. Nothing outside navigation/ may know which mode is running."""

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from playwright.async_api import Page


@dataclass
class OpenedListing:
    """A posting that is open in a browser tab, ready for our code to extract text from."""

    job_id: str | None  # None: no ID in the URL; caller falls back to title + client + date
    url: str  # normalized (no query string or fragment)
    page: Page
    listed_date: date | None = None  # date shown in the site's result list, if any


class Navigator(Protocol):
    async def login(self) -> None:
        """Log in with our own code (never the agent). A no-op for sites without login."""

    async def next_listing(self) -> OpenedListing | None:
        """Open the next not-yet-seen listing and return it, or None when the site is done.
        Listings whose ID is already in the database are skipped without being opened."""

    async def close_listing(self, listing: OpenedListing) -> None:
        """Close the listing's tab."""
