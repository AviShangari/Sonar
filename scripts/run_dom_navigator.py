"""Live run of the DOM navigator on one site: an agent reads the list page, then our code opens
each NEW posting, extracts it and stores it. Read-only on the site.

Run:  $env:PYTHONPATH="src"; .venv\\Scripts\\python.exe scripts/run_dom_navigator.py [site-name] [--limit N]
A browser window opens. Default limit is 5 postings so a test run stays cheap.
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, timedelta

from sonar.db.queries import add_listing, fail_stale_runs, finish_run, listing_exists, newest_listed_date, start_run
from sonar.db.tables import make_engine
from sonar.extraction.posting import extract_listing
from sonar.navigation.dom import DomNavigator, agent_collector
from sonar.navigation.session import open_browser
from sonar.sites import load_sites

PORT = 9333


async def main(name: str | None, limit: int, days: int | None) -> None:
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if os.environ.get(var):
            sys.exit(f"{var} is set; unset it so the run uses your subscription login.")
    sites = {s.name: s for s in load_sites()}
    site = sites[name] if name else next(iter(sites.values()))
    engine = make_engine()
    if closed := fail_stale_runs(engine):
        print(f"marked {closed} interrupted earlier run(s) as failed")
    run_id = start_run(engine)
    blocked: list[str] = []
    stored = 0
    try:
        async with open_browser(site, port=PORT, blocked=blocked) as context:
            cutoff = newest_listed_date(engine, site.name) or date.today() - timedelta(days=days if days is not None else site.first_run_days)
            print("collecting postings dated", cutoff, "or later")
            navigator = DomNavigator(
                site, context, agent_collector(PORT),
                is_seen=lambda job_id: listing_exists(engine, site.name, job_id), cutoff=cutoff,
            )
            await navigator.login()
            while stored < limit and (opened := await navigator.next_listing()):
                try:
                    listing = await extract_listing(site.name, opened)
                    add_listing(engine, listing, run_id)
                    stored += 1
                    print(f"stored {listing.job_id}: {listing.title!r} ({len(listing.posting_text)} chars)")
                except Exception as exc:  # sites fail independently; so do postings
                    print(f"FAILED {opened.url}: {exc}")
                finally:
                    await navigator.close_listing(opened)
            print(f"done: {stored} stored, {navigator.skipped_seen} already in the database")
            print("blocked by guard:", blocked)
        finish_run(engine, run_id, "ok")
    except BaseException as exc:  # includes Ctrl+C, so a stopped run is never left as "running"
        finish_run(engine, run_id, "failed", repr(exc))
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("site", nargs="?")
    parser.add_argument("--limit", type=int, default=5, help="max postings to store (test cap)")
    parser.add_argument("--days", type=int, help="first-run window in days, when nothing is stored yet")
    args = parser.parse_args()
    asyncio.run(main(args.site, args.limit, args.days))
