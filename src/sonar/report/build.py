"""Builds the report for one run from the database. Plain code: no agent writes the report."""

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sonar.db.queries import matched_listings
from sonar.db.tables import DecisionRow, ListingRow, RunRow
from sonar.models import KeywordHit, Report, ReportMatch


def _rank_key(match: ReportMatch) -> tuple[int, int]:
    """More different keywords first, then more mentions in total."""
    return (-len(match.hits), -sum(hit.count for hit in match.hits))


def build_report(engine: Engine, run_id: int, matcher: str) -> Report:
    """The report for a run: counts, failed sites (if any) and the matches that run found.
    Always produced, even with zero matches."""
    with Session(engine) as session:
        run = session.get(RunRow, run_id)
        if run is None:
            raise ValueError(f"no run {run_id}")
        new_postings = session.scalar(select(func.count()).select_from(ListingRow).where(ListingRow.run_id == run_id))
        scored = session.scalar(
            select(func.count()).select_from(DecisionRow).where(DecisionRow.run_id == run_id, DecisionRow.matcher == matcher)
        )
        status, started_at, notes = run.status, run.started_at, run.notes

    matches = [
        ReportMatch(
            title=listing.title or listing.url,
            url=listing.url,
            site=listing.site,
            job_id=listing.job_id,
            listed_date=(listing.fields or {}).get("listed_date"),
            posted_date=(listing.fields or {}).get("posted_date"),
            hits=[KeywordHit(**hit) for hit in decision.details.get("hits", [])],
        )
        for listing, decision in matched_listings(engine, run_id)
        if decision.matcher == matcher
    ]
    matches.sort(key=lambda m: m.listed_date or "", reverse=True)  # newest first among equals
    matches.sort(key=_rank_key)  # stable: keeps the date order inside each rank
    return Report(
        run_id=run_id,
        status=status,
        started_at=started_at.strftime("%Y-%m-%d %H:%M UTC"),
        failed_sites=notes if status in ("partial", "failed") else None,
        new_postings=new_postings or 0,
        scored=scored or 0,
        matcher=matcher,
        matches=matches,
    )
