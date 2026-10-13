"""Screenshots saved when navigation fails, so a human can see what the browser saw.

They go to `data/screenshots/` (gitignored). They are for people only: never put one in a prompt,
a report or an email, because a logged-in page can show private information.

Taking a screenshot must never make a failure worse: every method here catches its own errors
and returns None instead of raising.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import BrowserContext, Page

log = logging.getLogger("sonar.screenshots")

TIMEOUT_MS = 10_000  # a hung page must not hold up the run
DEFAULT_LIMIT = 10  # per site per run, so a site that fails on every posting cannot fill the disk


def _safe(text: str | None) -> str:
    """Make a piece of text safe for a file name: letters, digits, dot, dash, underscore."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text or "")[:60].strip("_")


class FailureShots:
    """One per site per run. Remembers how many it has taken and stops at `limit`."""

    def __init__(self, directory: Path, site: str, limit: int = DEFAULT_LIMIT) -> None:
        self.directory = directory
        self.site = site
        self.limit = limit
        self.taken = 0

    def _next_path(self, step: str, job_id: str | None) -> Path | None:
        if self.taken >= self.limit:
            log.warning("site=%s step=screenshot outcome=limit_reached limit=%d", self.site, self.limit)
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        # The counter keeps two failures in the same second from overwriting each other.
        parts = [stamp, _safe(self.site), _safe(step), _safe(job_id), str(self.taken + 1)]
        return self.directory / ("-".join(p for p in parts if p) + ".png")

    async def take(self, page: Page, step: str, job_id: str | None = None) -> Path | None:
        """Screenshot one page (the visible part). Returns the file, or None if it could not be saved."""
        try:
            if page.is_closed():
                return None
            path = self._next_path(step, job_id)
            if path is None:
                return None
            self.directory.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(path), timeout=TIMEOUT_MS)
            self.taken += 1
            log.info("site=%s job_id=%s step=screenshot outcome=saved path=%s", self.site, job_id, path)
            return path
        except Exception as exc:
            log.warning("site=%s job_id=%s step=screenshot outcome=failed error=%s", self.site, job_id, exc)
            return None

    async def take_current(self, context: BrowserContext, step: str) -> Path | None:
        """Screenshot the most recently opened real page of the browser. Used when the failure is not
        tied to one posting (for example the list page could not be read): the agent's tab is the
        last page that is not blank."""
        try:
            pages = [p for p in context.pages if not p.is_closed() and p.url not in ("", "about:blank")]
        except Exception as exc:
            log.warning("site=%s step=screenshot outcome=failed error=%s", self.site, exc)
            return None
        return await self.take(pages[-1], step) if pages else None
