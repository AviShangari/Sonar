"""Entry point:  python -m sonar.main [--site NAME] [--days N] [--max-new N]

Runs the pipeline for every site in config/sites.yaml and prints what matched.
Writes the run's report (.html and .txt) to the report folder. Sending it is not built yet.
"""

import argparse
import asyncio
import logging
import os
import sys

from sonar.config import load_settings
from sonar.db.queries import fail_stale_runs, finish_run, start_run
from sonar.db.tables import make_engine
from sonar.matching.factory import build_matcher
from sonar.pipeline import SiteOutcome, run_site
from sonar.report.build import build_report
from sonar.report.render import write_report
from sonar.sites import load_sites


def run_status(outcomes: list[SiteOutcome]) -> str:
    failed = [o for o in outcomes if o.error]
    if not failed:
        return "ok"
    return "failed" if len(failed) == len(outcomes) else "partial"


async def run(site_name: str | None, days: int | None, max_new: int | None) -> int:
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if os.environ.get(var):
            sys.exit(f"{var} is set; unset it so the run uses your subscription login.")
    settings = load_settings()
    sites = [s for s in load_sites() if site_name in (None, s.name)]
    if not sites:
        sys.exit(f"no site named '{site_name}' in config/sites.yaml")
    engine = make_engine(settings.database_url)
    if closed := fail_stale_runs(engine):
        print(f"marked {closed} interrupted earlier run(s) as failed")
    matcher = build_matcher(settings)
    run_id = start_run(engine)
    outcomes: list[SiteOutcome] = []
    try:
        for site in sites:  # one after another; each site fails on its own
            outcomes.append(await run_site(site, settings, engine, matcher, run_id, days, max_new))
    except BaseException as exc:  # includes Ctrl+C, so a stopped run is never left as "running"
        finish_run(engine, run_id, "failed", repr(exc))
        raise
    notes = "; ".join(f"{o.site}: {o.error}" for o in outcomes if o.error) or None
    status = run_status(outcomes)
    finish_run(engine, run_id, status, notes)
    print_summary(run_id, status, outcomes)
    for path in write_report(build_report(engine, run_id, settings.matcher), settings.report_path):
        print(f"report written: {path}")
    return 0 if status == "ok" else 1


def print_summary(run_id: int, status: str, outcomes: list[SiteOutcome]) -> None:
    print(f"\nrun {run_id}: {status}")
    for o in outcomes:
        if o.error:
            print(f"  {o.site}: FAILED - {o.error}")
            continue
        print(
            f"  {o.site}: {o.stored} new stored, {o.skipped_seen} already known, {o.failed_postings} failed, "
            f"{len(o.decisions or [])} scored, {o.matches} matches"
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Run the Sonar pipeline.")
    parser.add_argument("--site", help="only this site (default: all)")
    parser.add_argument("--days", type=int, help="first-run window in days, used only when nothing is stored yet")
    parser.add_argument("--max-new", type=int, help="TEST CAP on new postings per site. Not for real runs: it still moves the cutoff forward")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.site, args.days, args.max_new)))
