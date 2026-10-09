"""Small, single-purpose database functions."""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sonar.db.tables import DecisionRow, ListingRow, RunRow, utcnow
from sonar.models import Decision, Listing


def start_run(engine: Engine) -> int:
    with Session(engine) as session:
        run = RunRow()
        session.add(run)
        session.commit()
        return run.id


def finish_run(engine: Engine, run_id: int, status: str, notes: str | None = None) -> None:
    with Session(engine) as session:
        run = session.get(RunRow, run_id)
        run.status, run.notes, run.finished_at = status, notes, utcnow()
        session.commit()


def fail_stale_runs(engine: Engine, older_than: timedelta = timedelta(hours=2)) -> int:
    """Mark runs that started long ago and never finished (crashed or killed) as failed.
    Returns how many were closed. Call at the start of a run."""
    limit = utcnow() - older_than
    with Session(engine) as session:
        stale = session.scalars(
            select(RunRow).where(RunRow.status == "running", RunRow.finished_at.is_(None), RunRow.started_at < limit)
        ).all()
        for run in stale:
            run.status, run.finished_at, run.notes = "failed", utcnow(), "interrupted: never finished"
        session.commit()
        return len(stale)


def site_posting_texts(engine: Engine, site: str) -> list[str]:
    """All stored posting texts for a site (input for finding its boilerplate)."""
    with Session(engine) as session:
        return list(session.scalars(select(ListingRow.posting_text).where(ListingRow.site == site)))


def undecided_listings(engine: Engine, site: str, matcher: str) -> list[ListingRow]:
    """A site's stored listings that this matcher has not decided yet."""
    with Session(engine, expire_on_commit=False) as session:
        decided = select(DecisionRow.id).where(DecisionRow.listing_id == ListingRow.id, DecisionRow.matcher == matcher)
        query = select(ListingRow).where(ListingRow.site == site, ~decided.exists()).order_by(ListingRow.id)
        return list(session.scalars(query))


def listing_exists(engine: Engine, site: str, job_id: str) -> bool:
    """Dedup check: call this before extracting, matching or summarizing."""
    with Session(engine) as session:
        query = select(ListingRow.id).where(ListingRow.site == site, ListingRow.job_id == job_id)
        return session.scalar(query) is not None


def newest_listed_date(engine: Engine, site: str) -> date | None:
    """Newest result-list date among a site's stored listings (the stop point for collection)."""
    with Session(engine) as session:
        value = session.scalar(
            select(func.max(ListingRow.fields["listed_date"].as_string())).where(ListingRow.site == site)
        )
    return date.fromisoformat(value) if value else None


def add_listing(engine: Engine, listing: Listing, run_id: int) -> int:
    """Store a new listing and return its id. Raises IntegrityError on a duplicate."""
    with Session(engine) as session:
        row = ListingRow(**listing.model_dump(), run_id=run_id)
        session.add(row)
        session.commit()
        return row.id


def add_decision(engine: Engine, decision: Decision) -> int:
    with Session(engine) as session:
        row = DecisionRow(**decision.model_dump())
        session.add(row)
        session.commit()
        return row.id


def matched_listings(engine: Engine, run_id: int) -> list[tuple[ListingRow, DecisionRow]]:
    """Listings matched in a run: the input for the email report."""
    with Session(engine, expire_on_commit=False) as session:
        query = (
            select(ListingRow, DecisionRow)
            .join(DecisionRow, DecisionRow.listing_id == ListingRow.id)
            .where(DecisionRow.run_id == run_id, DecisionRow.matched.is_(True))
        )
        return [(listing, decision) for listing, decision in session.execute(query)]
