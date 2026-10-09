"""Keyword matcher: whole-word, case-insensitive, phrases match as exact word sequences."""

import re
from pathlib import Path

import yaml

from sonar.models import KeywordHit, MatchResult

SNIPPET_CONTEXT = 40  # characters kept on each side of a match


def load_keywords(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [str(k).strip() for k in data["keywords"] if str(k).strip()]


def compile_keyword(keyword: str) -> re.Pattern[str]:
    # Words of a phrase may be separated by any whitespace (web text has line breaks).
    body = r"\s+".join(re.escape(word) for word in keyword.split())
    # (?<!\w) / (?!\w): the match may not touch another letter or digit, so "SAP" misses "saplings".
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


def _snippet(text: str, start: int, end: int) -> str:
    left = max(0, start - SNIPPET_CONTEXT)
    right = min(len(text), end + SNIPPET_CONTEXT)
    excerpt = " ".join(text[left:right].split())  # collapse line breaks and runs of spaces
    return ("..." if left > 0 else "") + excerpt + ("..." if right < len(text) else "")


class KeywordMatcher:
    def __init__(self, keywords: list[str]) -> None:
        self._patterns = [(keyword, compile_keyword(keyword)) for keyword in keywords]

    def match(self, text: str) -> MatchResult:
        hits: list[KeywordHit] = []
        for keyword, pattern in self._patterns:  # every keyword is checked
            found = list(pattern.finditer(text))
            if found:
                first = found[0]
                hits.append(KeywordHit(keyword=keyword, count=len(found), snippet=_snippet(text, first.start(), first.end())))
        return MatchResult(hits=hits)
