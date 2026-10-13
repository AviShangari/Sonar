"""Live check of a site's login. Opens the dedicated browser (headed, so you can watch), logs in with
our login code, and prints where it ended up: the address, the tab title and the names of the links
on the page (no page text, nothing about the account). Needs SONAR_<SITE>_USERNAME and
SONAR_<SITE>_PASSWORD in .env.

    .venv\Scripts\python.exe scripts/check_login.py ontariotenders
"""

import asyncio
import sys

from sonar.navigation.guards import GuardState
from sonar.navigation.login import LoginFailed, log_in
from sonar.navigation.session import open_browser
from sonar.sites import load_sites


async def main(name: str) -> int:
    site = {s.name: s for s in load_sites()}[name]
    guard, blocked = GuardState(), []
    async with open_browser(site, port=9340, headless=False, blocked=blocked, guard=guard) as context:
        try:
            await log_in(context, site, guard)
        except LoginFailed as exc:
            print("LOGIN FAILED:", exc)
            return 1
        page = context.pages[-1]
        print("logged in. address:", page.url)
        print("tab title:", await page.title())
        names = await page.eval_on_selector_all("a", "els => els.map(e => e.innerText.trim()).filter(t => t && t.length < 50)")
        print("links on the page:", sorted(set(names))[:40])
        print("blocked by the guards:", blocked)
        await asyncio.sleep(5)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
