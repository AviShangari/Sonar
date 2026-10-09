"""Browser guards: the firm safety boundary for every navigator.

Installed on the whole browser context with Playwright's route(), so they apply to every
request, whoever triggered it (the agent's clicks, page scripts, redirects).
Rules: only GET/HEAD/OPTIONS requests; page navigations only to allowed hosts.
Login is the one exception to the method rule, and it is done by our code, not the agent.
"""

from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Route

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_allowed(method: str, url: str, is_navigation: bool, allowed_hosts: set[str]) -> bool:
    """Pure decision function, so the rules can be tested without a browser."""
    if method.upper() not in SAFE_METHODS:
        return False
    if is_navigation:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and (parsed.hostname or "") in allowed_hosts
    return True


async def install_guards(context: BrowserContext, allowed_hosts: list[str], blocked: list[str] | None = None) -> None:
    """Route every request through is_allowed. Refused requests are appended to `blocked` (for logs)."""
    hosts = set(allowed_hosts)

    async def handler(route: Route) -> None:
        req = route.request
        if is_allowed(req.method, req.url, req.is_navigation_request(), hosts):
            await route.continue_()
        else:
            if blocked is not None:
                blocked.append(f"{req.method} {req.url}")
            await route.abort("blockedbyclient")

    await context.route("**/*", handler)
