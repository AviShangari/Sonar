"""Entry point:  python -m sonar.main [--site NAME] [--days N] [--max-new N]

Runs the pipeline for every site in config/sites.yaml and prints what matched.
Writes the run's report (.html and .txt) to the report folder, and emails it when `email: true`.
"""

import argparse
import asyncio
import logging
import os
import sys

from sqlalchemy.engine import Engine

from sonar import budget, llm
from sonar.config import Settings, load_settings
from sonar.db.queries import fail_stale_runs, finish_run, start_run
from sonar.db.tables import make_engine
from sonar.matching.factory import build_matcher
from sonar.pipeline import SiteOutcome, run_site
from sonar.report.build import build_report
from sonar.report.render import write_report
from sonar.report.send import send_alert, send_report
from sonar.runlock import RunLocked, run_lock
from sonar.summarize.summaries import summarize_matches
from sonar.sites import SiteConfig, load_sites


def run_status(outcomes: list[SiteOutcome]) -> str:
    failed = [o for o in outcomes if o.error]
    if not failed:
        return "ok"
    return "failed" if len(failed) == len(outcomes) else "partial"


async def run(site_name: str | None, days: int | None, max_new: int | None) -> int:
    settings = load_settings()
    if settings.auth == "subscription":
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            if os.environ.get(var):
                sys.exit(f"{var} is set; unset it so the run uses your subscription login (or set auth: openrouter).")
    try:
        llm.configure(settings)  # fails before anything starts if the API key is missing
    except RuntimeError as exc:
        sys.exit(str(exc))
    sites = [s for s in load_sites() if site_name in (None, s.name)]
    if not sites:
        sys.exit(f"no site named '{site_name}' in config/sites.yaml")
    try:
        with run_lock("run", settings.lock_path):  # one run at a time
            return await run_locked(settings, sites, days, max_new)
    except RunLocked as exc:
        print(f"run skipped: {exc}")
        if settings.email:
            try:  # nobody is watching a scheduled run, so say so by email
                send_alert(settings, "[RUN SKIPPED] Sonar: another run was still in progress", f"{exc}\n\nThis run did nothing.")
            except Exception as mail_exc:
                print(f"alert email failed: {mail_exc}")
        return 2


async def run_locked(settings: Settings, sites: list[SiteConfig], days: int | None, max_new: int | None) -> int:
    engine = make_engine(settings.database_url)
    if closed := fail_stale_runs(engine):
        print(f"marked {closed} interrupted earlier run(s) as failed")
    matcher = build_matcher(settings)
    run_id = start_run(engine)
    budget.configure(budget.RunBudget.from_settings(settings))  # fresh counters for this run
    await budget.current.begin()
    outcomes: list[SiteOutcome] = []
    try:
        for site in sites:  # one after another; each site fails on its own
            if budget.current.exhausted:  # a run-wide cost limit was hit: do not start another site
                budget.current.note_hit(f"skipped:{site.name}", f"{site.name} was not started")
                continue
            outcomes.append(await run_site(site, settings, engine, matcher, run_id, days, max_new))
    except BaseException as exc:  # includes Ctrl+C, so a stopped run is never left as "running"
        finish_run(engine, run_id, "failed", repr(exc))
        deliver(engine, run_id, settings)  # a crashed run must not look like a quiet day
        raise
    # Navigation and summaries run one after the other, so the spend at this boundary splits the bill by model.
    nav_real, nav_sdk = await budget.current.final_spent_usd(), budget.current.sdk_cost_usd
    if settings.summaries:
        try:  # summaries are a bonus: a failure here must not fail the run
            print(f"summaries written: {await summarize_matches(engine, run_id, settings.matcher, workers=settings.summary_workers)}")
        except Exception as exc:
            print(f"summaries failed: {exc}")
    status = run_status(outcomes)
    problems = [f"{o.site}: {o.error}" for o in outcomes if o.error] + budget.current.notes()
    if status == "ok" and budget.current.hits:
        status = "partial"  # a cost limit cut the run short: say so, and the cut sites keep their watermark
    notes = "; ".join(problems) or None
    total_real = await budget.current.final_spent_usd()
    print(budget.current.summary_line(total_real))
    if nav_real is not None and total_real is not None:
        print(
            f"cost by model: navigation ({settings.navigation_model}) OpenRouter ${nav_real:.4f}, SDK ${nav_sdk:.4f}; "
            f"summaries ({settings.summary_model}) OpenRouter ${max(0.0, total_real - nav_real):.4f}, "
            f"SDK ${budget.current.sdk_cost_usd - nav_sdk:.4f}"
        )
    finish_run(engine, run_id, status, notes)
    print_summary(run_id, status, outcomes)
    deliver(engine, run_id, settings)
    return 0 if status == "ok" else 1


def deliver(engine: Engine, run_id: int, settings: Settings) -> None:
    """Write the report files and send the email. Never raises: it also runs while a crash is being
    reported, and a delivery problem must not hide the original error."""
    try:
        report = build_report(engine, run_id, settings.matcher, settings.tz)
        for path in write_report(report, settings.report_path):
            print(f"report written: {path}")
        if settings.email:
            send_report(report, settings)
            print(f"email sent to {settings.email_to}")
    except Exception as exc:
        print(f"report or email failed: {exc}")


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
    parser.add_argument("--max-new", type=int, help="TEST CAP on new postings per site. Safe to use: a capped run does not move the stop point")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.site, args.days, args.max_new)))
