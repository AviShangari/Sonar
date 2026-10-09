"""Pydantic schemas shared across modules."""

from datetime import datetime

from pydantic import BaseModel


class KeywordHit(BaseModel):
    """One keyword found in a listing."""

    keyword: str
    count: int
    snippet: str  # short excerpt around the first occurrence


class MatchResult(BaseModel):
    """What a matcher says about one listing."""

    hits: list[KeywordHit]

    @property
    def matched(self) -> bool:
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
