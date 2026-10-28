"""Builds the report for one run from the database. Plain code: no agent writes the report."""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sonar.db.queries import matched_listings
from sonar.extraction.documents import has_document_links
from sonar.db.tables import DecisionRow, ListingRow, RunRow
from sonar.models import KeywordHit, Report, ReportMatch
from sonar.report.render import portal_name


REVIEW_NOTE = "Please review the attached documents in the listing to verify that this listing is relevant."


def _rank_key(match: ReportMatch) -> tuple[int, int, float]:
    """More different keywords first, then more mentions in total, then the higher Jev score
    (so "needs review" matches sort below confident ones)."""
    return (-len(match.hits), -sum(hit.count for hit in match.hits), -(match.score or 0.0))


SITE_PREFIX = re.compile(r"^([a-z][a-z0-9_-]*): ")


def failed_portals(notes: str | None) -> list[str]:
    """Readable portal names from a run's notes ("canadabuys: OpenFailures: ...; ariba: ..."), each once, in order.
    Cost-limit notes carry no site prefix, so they name no portal."""
    names: list[str] = []
    for part in (notes or "").split("; "):
        if found := SITE_PREFIX.match(part):
            name = portal_name(found.group(1))
            if name not in names:
                names.append(name)
    return names


def closing_text(fields: dict) -> str | None:
    """How the closing date is shown. None when the posting was not read yet (no summary)."""
    if "closing_unsure" not in fields:
        return None
    date = fields.get("closing_date")
    if not date:
        return "not found, please verify"
    return f"{date} (unsure, please verify)" if fields.get("closing_unsure") else date


def _long_date_time(moment: datetime) -> str:
    """October 15, 2026, 09:00 EDT. Built by hand because %-d (no leading zero) does not work on Windows."""
    return f"{moment:%B} {moment.day}, {moment.year}, {moment:%H:%M %Z}"


def checked_text(fields: dict, key: str, missing: str) -> str | None:
    """A summary-agent field as shown: the value, flagged if it was not found in the posting, or `missing`
    when the posting does not give it. None when the posting was not read yet."""
    if "unverified" not in fields:
        return None
    value = fields.get(key)
    if not value:
        return missing
    return f"{value} (unsure, please verify)" if key in fields["unverified"] else value


def build_report(
    engine: Engine,
    run_id: int,
    matcher: str,
    tz: ZoneInfo | None = None,
    note_sites: frozenset[str] = frozenset(),
    accounts: dict[str, str] | None = None,
) -> Report:
    """The report for a run: counts, failed sites (if any) and the matches that run found.
    Always produced, even with zero matches. The run's start time is shown in `tz` (UTC if not given);
    the database itself always stores UTC. Matches get the 'review the documents' note when their site is in
    `note_sites` (sites.yaml `attachment_note`) or their saved page links to document files. `accounts`
    (site name to sites.yaml `account` label) tells each match which login found it."""
    accounts = accounts or {}
    with Session(engine) as session:
        run = session.get(RunRow, run_id)
        if run is None:
            raise ValueError(f"no run {run_id}")
        new_postings = session.scalar(select(func.count()).select_from(ListingRow).where(ListingRow.run_id == run_id))
        decisions_of_run = select(func.count()).select_from(DecisionRow).where(
            DecisionRow.run_id == run_id, DecisionRow.matcher == matcher
        )
        scored = session.scalar(decisions_of_run.where(DecisionRow.matched.is_not(None)))
        unscored = session.scalar(decisions_of_run.where(DecisionRow.matched.is_(None)))
        status, started_at, notes = run.status, run.started_at, run.notes

    matches = [
        ReportMatch(
            title=listing.title or listing.url,
            url=listing.url,
            site=listing.site,
            job_id=listing.job_id,
            listed_date=(listing.fields or {}).get("listed_date"),
            posted_date=(listing.fields or {}).get("posted_date"),
            summary=(listing.fields or {}).get("summary"),
            how_to_apply=(listing.fields or {}).get("how_to_apply") or None,
            closing_date=closing_text(listing.fields or {}),
            solicitation_number=checked_text(listing.fields or {}, "solicitation_number", "not found, please verify"),
            supply_arrangement=checked_text(listing.fields or {}, "supply_arrangement", "N/A"),
            department=checked_text(listing.fields or {}, "department", "not found, please verify"),
            review_note=REVIEW_NOTE if listing.site in note_sites or has_document_links(listing.raw_html) else None,
            account=accounts.get(listing.site),
            score=decision.score,
            needs_review=bool(decision.details.get("needs_review")),
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
        started_at=_long_date_time(started_at.replace(tzinfo=timezone.utc).astimezone(tz or timezone.utc)),
        failed_sites=notes if status in ("partial", "failed") else None,
        failed_portals=failed_portals(notes) if status in ("partial", "failed") else [],
        new_postings=new_postings or 0,
        scored=scored or 0,
        unscored=unscored or 0,
        matcher=matcher,
        matches=matches,
    )
