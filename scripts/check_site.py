"""Smoke check for a site entry: launch the guarded browser, load the start page once,
and show what generic rules find. Read-only. Run:  .venv\\Scripts\\python.exe scripts/check_site.py [site-name]"""

import asyncio
import sys

from sonar.navigation.session import open_browser
from sonar.navigation.urls import job_id_from_url, normalize_url
from sonar.sites import load_sites


async def main(name: str | None) -> None:
    sites = {s.name: s for s in load_sites()}
    site = sites[name] if name else next(iter(sites.values()))
    blocked: list[str] = []
    async with open_browser(site, blocked=blocked) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(site.start_url, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle")
        print("title:", await page.title())
        hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        links = sorted({normalize_url(h) for h in hrefs})
        with_id = [(u, job_id_from_url(u)) for u in links if job_id_from_url(u)]
        print(f"links: {len(links)} unique, {len(with_id)} with an ID-like address")
        for url, job_id in with_id[:10]:
            print(f"  {job_id}  {url}")
        print("blocked by guard:", blocked)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
