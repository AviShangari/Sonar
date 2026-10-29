"""What goes into a PDF, read from the database (read-only is enough): the run's report, per-portal counts and the
closest rejections. Plain code; the matches come from the same build_report the email report uses."""

import re
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sonar.config import Settings
from sonar.db.tables import DecisionRow, ListingRow, RunRow
from sonar.models import Report, ReportMatch
from sonar.pdf.spec import PdfSpec
from sonar.report.build import build_report
from sonar.report.render import portal_name

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
NO_NEAR_MISSES_FOR_KEYWORDS = "Near misses need the Jev matcher, which gives every rejected posting a score. The keyword matcher does not."


class SiteCount(BaseModel):
    site: str
    portal: str
    new_postings: int = 0  # first seen in this run
    matched: int = 0
    needs_review: int = 0  # matched, but flagged "needs review"
    rejected: int = 0
    unscored: int = 0


class NearMiss(BaseModel):
    title: str
    url: str
    site: str
    score: float
    listed_date: str | None = None


class PdfData(BaseModel):
    report: Report  # the run, with only the matches the PDF shows (filtered, sorted, capped)
    matches_in_run: int  # every match of the run, before the filters
    matches_after_filter: int  # after the filters, before the cap
    site_counts: list[SiteCount]
    rejected_total: int
    near_misses: list[NearMiss]
    near_miss_note: str | None = None  # why there are none, when that is not simply "nothing was close"
    match_threshold: float
    review_threshold: float
    generated_at: str


def latest_run_id(engine: Engine) -> int:
    with Session(engine) as session:
        run_id = session.scalar(select(func.max(RunRow.id)))
    if run_id is None:
        raise ValueError("no run is recorded yet, so there is nothing to put in a PDF")
    return run_id


def _closing_key(match: ReportMatch) -> str:
    """Sort key for the closing date: the first ISO date in the text; matches without one go last."""
    found = ISO_DATE.search(match.closing_date or "")
    return found.group() if found else "9999-99-99"


def _wanted_site(site: str, wanted: list[str]) -> bool:
    return not wanted or site.lower() in wanted or portal_name(site).lower() in wanted


def _choose_matches(matches: list[ReportMatch], spec: PdfSpec) -> list[ReportMatch]:
    """The report's matches (already in rank order) after the spec's site and keyword filters and its sort."""
    chosen = [m for m in matches if _wanted_site(m.site, spec.sites)]
    if spec.keyword:
        needle = spec.keyword.lower()
        chosen = [
            m for m in chosen
            if needle in " ".join([*(h.keyword for h in m.hits), m.title, m.summary or ""]).lower()
        ]
    if spec.sort == "closing":
        chosen.sort(key=_closing_key)
    elif spec.sort == "site":
        chosen.sort(key=lambda m: (portal_name(m.site), m.site))
    elif spec.sort == "title":
        chosen.sort(key=lambda m: m.title.lower())
    return chosen


def collect(
    engine: Engine, settings: Settings, spec: PdfSpec, note_sites: frozenset[str] = frozenset(),
    accounts: dict[str, str] | None = None,
) -> PdfData:
    run_id = spec.run_id if spec.run_id is not None else latest_run_id(engine)
    report = build_report(engine, run_id, settings.matcher, settings.tz, note_sites, accounts)  # ValueError: no such run
    with Session(engine) as session:
        new_per_site = dict(session.execute(
            select(ListingRow.site, func.count()).where(ListingRow.run_id == run_id).group_by(ListingRow.site)
        ).all())
        decided = session.execute(
            select(DecisionRow.listing_id, DecisionRow.matched, DecisionRow.score, DecisionRow.details,
                   ListingRow.site, ListingRow.title, ListingRow.url, ListingRow.fields)
            .join(ListingRow, ListingRow.id == DecisionRow.listing_id)
            .where(DecisionRow.run_id == run_id, DecisionRow.matcher == settings.matcher)
        ).all()

    counts = {site: SiteCount(site=site, portal=portal_name(site), new_postings=n) for site, n in new_per_site.items()}
    near: list[NearMiss] = []
    rejected_total = 0
    for row in decided:
        count = counts.setdefault(row.site, SiteCount(site=row.site, portal=portal_name(row.site)))
        if row.matched is True:
            count.matched += 1
            count.needs_review += bool((row.details or {}).get("needs_review"))
        elif row.matched is False:
            count.rejected += 1
            rejected_total += 1
            if row.score is not None and _wanted_site(row.site, spec.sites):
                near.append(NearMiss(
                    title=row.title or row.url, url=row.url, site=row.site, score=row.score,
                    listed_date=(row.fields or {}).get("listed_date"),
                ))
        else:
            count.unscored += 1
    near.sort(key=lambda n: (-n.score, n.title.lower()))

    chosen = _choose_matches(report.matches, spec)
    now = datetime.now(settings.tz)
    return PdfData(
        report=report.model_copy(update={"matches": chosen[: spec.max_matches]}),
        matches_in_run=len(report.matches),
        matches_after_filter=len(chosen),
        site_counts=sorted(counts.values(), key=lambda c: (c.portal, c.site)),
        rejected_total=rejected_total,
        near_misses=near[: spec.max_near_misses],
        near_miss_note=NO_NEAR_MISSES_FOR_KEYWORDS if settings.matcher != "jev" else None,
        match_threshold=settings.jev_match_threshold,
        review_threshold=settings.jev_review_threshold,
        generated_at=f"{now:%Y-%m-%d %H:%M %Z}",
    )
