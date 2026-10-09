"""Pydantic schemas shared across modules."""

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
