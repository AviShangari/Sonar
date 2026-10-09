"""The interface every matcher implements (keywords now, Jev later)."""

from typing import Protocol

from sonar.models import MatchResult


class Matcher(Protocol):
    def match(self, text: str) -> MatchResult: ...
