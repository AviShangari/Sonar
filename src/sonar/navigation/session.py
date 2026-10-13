"""Launches the DEDICATED browser for one site. Never attaches to your personal Chrome."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import BrowserContext, Playwright, async_playwright

from sonar.navigation.guards import GuardState, install_guards
from sonar.navigation.urls import normalize_url
from sonar.sites import SiteConfig

log = logging.getLogger("sonar.browser")

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"


async def _normal_user_agent(pw: Playwright) -> str:
    """The browser's own user agent without the "HeadlessChrome" tag. Some sites (CanadaBuys)
    answer 403 to that tag. Read from the installed Chromium so version and OS stay true."""
    probe = await pw.chromium.launch(headless=True)
    try:
        page = await probe.new_page()
        agent: str = await page.evaluate("navigator.userAgent")
    finally:
        await probe.close()
    return agent.replace("HeadlessChrome", "Chrome")


@asynccontextmanager
async def open_browser(
    site: SiteConfig, port: int = 9333, headless: bool = False, blocked: list[str] | None = None,
    guard: GuardState | None = None,
) -> AsyncIterator[BrowserContext]:
    """Own profile folder per site under data/, a remote-debugging port (so the browser-use
    CLI can attach over CDP), and the guards installed before anything can load.
    Headed locally, headless on the VM (with the "Headless" tag removed from the user agent).
    `guard` is the shared guard state; login code uses it to open the short login window."""
    profile = DATA_DIR / "profiles" / site.name
    profile.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(profile),
            headless=headless,
            args=[f"--remote-debugging-port={port}"],
            user_agent=await _normal_user_agent(pw) if headless else None,
        )
        await install_guards(context, site.allowed_hosts, blocked, guard, site.read_post_paths)

        def log_http_error(response) -> None:
            """Every HTTP 4xx/5xx the browser gets, whoever caused it (the agent, our code, the page's own scripts).
            Address only (no query string): enough to see which pages refuse us, nothing private."""
            if response.status >= 400:
                log.info(
                    "site=%s step=http_error status=%d type=%s url=%s",
                    site.name, response.status, response.request.resource_type, normalize_url(response.url),
                )

        context.on("response", log_http_error)
        try:
            yield context
        finally:
            await context.close()
