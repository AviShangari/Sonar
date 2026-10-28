"""Pydantic schemas shared across modules."""

from datetime import datetime

from pydantic import BaseModel


class KeywordHit(BaseModel):
    """One keyword found in a listing."""

    keyword: str
    count: int
    snippet: str  # short excerpt around the first occurrence


class MatchResult(BaseModel):
    """What a matcher says about one listing.

    The keyword matcher fills `hits`; "matched" then means at least one hit. Jev fills `score` (the probability
    of "yes") and sets `verdict` itself, because it has no hits. `unscored` means the matcher could not decide
    (Jev down): that is neither a match nor a non-match."""

    hits: list[KeywordHit] = []
    score: float | None = None  # Jev: probability that the posting is relevant, 0 to 1
    verdict: bool | None = None  # set by matchers that decide without hits; None = decided by the hits
    needs_review: bool = False  # a match the matcher is not confident about (Jev score between the two thresholds)
    unscored: bool = False
    error: str | None = None  # why it could not be scored (never holds a secret)
    cost_usd: float | None = None  # what the matcher's service charged for this posting

    @property
    def matched(self) -> bool | None:
        if self.unscored:
            return None
        if self.verdict is not None:
            return self.verdict
        return bool(self.hits)


class Listing(BaseModel):
    """A posting as collected from a site. Raw data is kept for every listing."""

    site: str
    job_id: str
    url: str
    title: str | None = None
    posting_text: str
    raw_html: str | None = None
    fields: dict[str, str | None] = {}  # extracted fields; missing ones are None
    text_hash: str


class Decision(BaseModel):
    """A matcher's verdict on one stored listing.

    matched is None when the listing could not be scored (e.g. Jev is down):
    that means "unscored", never "not a match".
    """

    listing_id: int
    run_id: int
    matcher: str  # "keywords" or "jev"
    matched: bool | None
    score: float | None = None  # Jev probability; unused by keywords
    details: dict = {}  # e.g. keyword hits with snippets


class RunLog(BaseModel):
    """One execution of the pipeline."""

    id: int
    started_at: datetime
    finished_at: datetime | None = None
    status: str  # "running", "ok", "partial" (some sites failed) or "failed"
    notes: str | None = None


class ReportMatch(BaseModel):
    """One matched listing as shown in the report."""

    title: str
    url: str
    site: str
    job_id: str
    listed_date: str | None = None  # date on the site's result list
    posted_date: str | None = None  # date in the posting itself
    summary: str | None = None  # short agent-written summary; None if it could not be written
    how_to_apply: str | None = None  # as the posting says; None if not stated or no summary
    closing_date: str | None = None  # what to show: "2026-12-31", "2026-12-31 (unsure, please verify)" or None
    solicitation_number: str | None = None  # None if the posting was not read; may end "(unsure, please verify)"
    supply_arrangement: str | None = None  # "N/A" when none applies
    department: str | None = None
    review_note: str | None = None  # set when the posting has (or probably has) documents the reader should open
    account: str | None = None  # which login found it (sites.yaml `account`), so a tester opens it with the right one
    score: float | None = None  # Jev probability; None for keyword matches
    needs_review: bool = False  # Jev was not confident: shown in the same list, with a flag
    hits: list[KeywordHit] = []


class Report(BaseModel):
    """Everything the report shows for one run. Built from the database, rendered to HTML and text."""

    run_id: int
    status: str  # "ok", "partial" or "failed"
    started_at: str
    failed_sites: str | None = None  # the run's notes when sites failed (for the developer: not shown in the email)
    failed_portals: list[str] = []  # readable names of the portals that could not be checked, for the email's short note
    new_postings: int  # stored during this run
    scored: int  # listings the matcher decided during this run
    unscored: int = 0  # listings the matcher could not score this run (Jev down); they are retried next run
    matcher: str
    matches: list[ReportMatch]  # ranked, best first
