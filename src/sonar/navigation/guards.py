"""Browser guards: the firm safety boundary for every navigator.

Installed on the whole browser context with Playwright's route(), so they apply to every
request, whoever triggered it (the agent's clicks, page scripts, redirects).
Rules: only GET/HEAD/OPTIONS requests; page navigations only to allowed hosts.
There are exactly two exceptions to the method rule:
  1. Login, done by our code, not the agent: our login code opens a short "login window" (GuardState)
     in which a few POST requests to the site's own allowed hosts are let through, and closes it again
     the moment the form is submitted.
  2. Read-only POST paths: some sites fetch list rows with a POST (sorting, paging). The developer can list
     the exact paths in sites.yaml (`read_post_paths`) after checking that they only read. A POST passes only
     if its path matches one of them exactly, on one of the site's allowed hosts.
"""

from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Route

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOGIN_POST_LIMIT = 3  # a login may involve a redirect or two; more than this is not a login


class GuardState:
    """Shared between the guards and the login code. Closed by default; only login code opens it."""

    def __init__(self) -> None:
        self.login_open = False
        self.posts_left = 0

    def open_login_window(self) -> None:
        self.login_open, self.posts_left = True, LOGIN_POST_LIMIT

    def close_login_window(self) -> None:
        self.login_open, self.posts_left = False, 0


def is_allowed(
    method: str, url: str, is_navigation: bool, allowed_hosts: set[str], login_post_ok: bool = False,
    read_post_paths: frozenset[str] = frozenset(),
) -> bool:
    """Pure decision function, so the rules can be tested without a browser.
    `login_post_ok`: our login code has the login window open, so a POST to an allowed host may pass.
    `read_post_paths`: exact paths the developer approved as read-only POSTs (only on allowed hosts)."""
    if method.upper() not in SAFE_METHODS:
        parsed = urlparse(url)
        if (parsed.hostname or "") not in allowed_hosts:
            return False
        if method.upper() == "POST" and parsed.path in read_post_paths:
            return True
        return login_post_ok
    if is_navigation:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and (parsed.hostname or "") in allowed_hosts
    return True


async def install_guards(
    context: BrowserContext, allowed_hosts: list[str], blocked: list[str] | None = None, state: GuardState | None = None,
    read_post_paths: list[str] | None = None,
) -> None:
    """Route every request through is_allowed. Refused requests are appended to `blocked` (for logs)."""
    hosts = set(allowed_hosts)
    state = state or GuardState()
    read_paths = frozenset(read_post_paths or [])

    async def handler(route: Route) -> None:
        req = route.request
        login_post_ok = state.login_open and state.posts_left > 0
        if is_allowed(req.method, req.url, req.is_navigation_request(), hosts, login_post_ok, read_paths):
            if req.method.upper() not in SAFE_METHODS and urlparse(req.url).path not in read_paths:
                state.posts_left -= 1  # a login POST was let through
            await route.continue_()
        else:
            if blocked is not None:
                blocked.append(f"{req.method} {req.url}")
            await route.abort("blockedbyclient")

    await context.route("**/*", handler)
