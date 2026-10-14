"""Login, done by our code and never by the agent. Generic: no selectors written for one site.

The form is found by what every login form has: a visible password box, the nearest text or email
box before it, and the form's submit button. The username and password come from secrets.py and
go only into those two boxes. They are never logged, put in a prompt or given to the browser-use
CLI. The agent has no typing helper, and our login finishes before any agent starts.

The browser guards block every non-GET request. For the one form submission our code opens a short
"login window" (guards.GuardState) and closes it again straight afterwards.

Success means the password box is gone. A failed login is never retried (that can lock an
account): it raises LoginFailed, the site stops, and the run's email says why.
"""

import logging

from playwright.async_api import BrowserContext, Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from sonar.navigation.guards import GuardState
from sonar.navigation.screenshots import FailureShots
from sonar.secrets import site_credentials
from sonar.sites import SiteConfig

log = logging.getLogger("sonar.login")

PAGE_TIMEOUT_MS = 30_000
SUBMIT_TIMEOUT_MS = 15_000  # how long to wait for the page to react to the submitted form
PASSWORD_BOX = "input[type=password]"
TEXT_BOXES = (
    "input:not([type=password]):not([type=hidden]):not([type=submit]):not([type=button])"
    ":not([type=checkbox]):not([type=radio]):not([type=image]):not([type=reset])"
)
SUBMIT_BUTTONS = "input[type=submit], button[type=submit], button:not([type]), input[type=image]"


class LoginFailed(RuntimeError):
    """The site could not be logged in to. Never contains the username or password."""


async def has_password_box(page: Page) -> bool:
    return await page.locator(PASSWORD_BOX).filter(visible=True).count() > 0


async def find_form(page: Page) -> tuple[Locator, Locator, Locator | None]:
    """(username box, password box, submit button or None) of the page's login form."""
    password = page.locator(PASSWORD_BOX).filter(visible=True).first
    if await password.count() == 0:
        raise LoginFailed("no password box on the login page")
    scope = password.locator("xpath=ancestor::form[1]")
    if await scope.count() == 0:
        scope = page.locator("body")  # inputs that are not inside a <form>
    candidates = scope.locator(TEXT_BOXES).filter(visible=True)
    username = None
    for i in range(await candidates.count()):
        box = candidates.nth(i)
        # the nearest box that comes BEFORE the password box on the page
        if await box.evaluate("(el, pw) => !!(el.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING)", await password.element_handle()):
            username = box
    if username is None:
        raise LoginFailed("no username box before the password box")
    submit = scope.locator(SUBMIT_BUTTONS).filter(visible=True)
    return username, password, submit.first if await submit.count() else None


async def log_in(context: BrowserContext, site: SiteConfig, guard: GuardState, shots: FailureShots | None = None) -> None:
    """Log in to `site`. A no-op for sites without a login. Leaves the page after login open in its
    tab (the navigator's agent starts from there). Raises LoginFailed; the login is tried once."""
    if not site.requires_login:
        return
    username, password_value = site_credentials(site.name)  # raises RuntimeError naming the missing variable
    page = await context.new_page()
    try:
        await page.goto(site.login_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
        if page.url.startswith("chrome-error:"):  # the browser's own error page has no password box either
            raise LoginFailed(f"the login page did not load (blocked or unreachable): {site.login_url}")
        if not await has_password_box(page):
            log.info("site=%s step=login outcome=already_logged_in", site.name)
            return
        user_box, password_box, submit = await find_form(page)
        await user_box.fill(username)
        await password_box.fill(password_value)
        guard.open_login_window()
        try:
            try:
                # The window stays open until the submission's navigation is done: the POST is sent a
                # moment AFTER the click returns, so closing earlier would block our own login.
                async with page.expect_navigation(wait_until="load", timeout=SUBMIT_TIMEOUT_MS):
                    if submit is not None:
                        await submit.click(timeout=PAGE_TIMEOUT_MS)
                    else:
                        await password_box.press("Enter")
            except PlaywrightTimeoutError:
                pass  # no page navigation (a script-driven login, or nothing happened): judged below
            await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
        finally:
            guard.close_login_window()  # always shut again, whatever happened
        if await has_password_box(page):
            raise LoginFailed(f"the login page is still showing after submitting (wrong credentials, or the form changed): {page.url}")
        log.info("site=%s step=login outcome=ok", site.name)
    except LoginFailed as exc:
        await _fail(page, shots)
        raise LoginFailed(f"{site.name}: {exc}") from None
    except Exception as exc:  # timeouts and the like; the message must never carry credentials
        await _fail(page, shots)
        raise LoginFailed(f"{site.name}: login did not complete ({type(exc).__name__})") from None


async def _fail(page: Page, shots: FailureShots | None) -> None:
    """Keep a picture of where the login stopped, without the typed values on it."""
    try:
        for box in await page.locator(f"{PASSWORD_BOX}, {TEXT_BOXES}").filter(visible=True).all():
            await box.fill("")  # the screenshot is for people, but it should still not show what was typed
    except Exception:
        pass
    if shots is not None:
        await shots.take(page, "login")
    try:
        await page.close()
    except Exception:
        pass
