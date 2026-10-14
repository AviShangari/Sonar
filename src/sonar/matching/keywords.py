"""Keyword matcher: whole-word, case-insensitive, phrases match as exact word sequences.

A keyword may carry `ignore_inside`: phrases it must not be counted in. "SAP" with
ignore_inside ["SAP Ariba"] still matches "SAP S/4HANA" but not "SAP Ariba" (a portal
the tender tells bidders to use, not an SAP project).

A keyword may also carry `needs_context: true` (for generic ones such as "Project Management",
which also appear in construction and property work). It then counts only if the posting
shows one of the client's areas (the `context_terms` list: recruitment, consulting, tech)
or also has a specific keyword hit (a keyword without needs_context)."""

import re
from pathlib import Path

import yaml
from pydantic import BaseModel

from sonar.models import KeywordHit, MatchResult

SNIPPET_CONTEXT = 40  # characters kept on each side of a match


class KeywordRule(BaseModel):
    keyword: str
    ignore_inside: list[str] = []  # a hit lying inside one of these phrases does not count
    needs_context: bool = False  # a generic keyword: counts only with a context term or a specific hit


def load_keywords(path: Path) -> list[KeywordRule]:
    """Each entry in keywords.yaml is a plain keyword, or {keyword: ..., ignore_inside: [...], needs_context: true}."""
    rules: list[KeywordRule] = []
    for entry in yaml.safe_load(path.read_text(encoding="utf-8"))["keywords"]:
        rule = KeywordRule(**entry) if isinstance(entry, dict) else KeywordRule(keyword=str(entry))
        rule.keyword = rule.keyword.strip()
        if rule.keyword:
            rules.append(rule)
    return rules


def load_context_terms(path: Path) -> list[str]:
    """The client's areas of work (recruitment, consulting, tech): words that give generic keywords their context."""
    terms = yaml.safe_load(path.read_text(encoding="utf-8")).get("context_terms") or []
    return [str(t).strip() for t in terms if str(t).strip()]


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
    def __init__(self, keywords: list[str | KeywordRule], context_terms: list[str] | None = None) -> None:
        rules = [KeywordRule(keyword=k) if isinstance(k, str) else k for k in keywords]
        if any(rule.needs_context for rule in rules) and not context_terms:
            raise ValueError("a keyword has needs_context but keywords.yaml has no context_terms")
        self._rules = rules
        self._patterns = [
            (rule.keyword, compile_keyword(rule.keyword), [compile_keyword(p) for p in rule.ignore_inside])
            for rule in rules
        ]
        self._context = [compile_keyword(term) for term in context_terms or []]

    def match(self, text: str) -> MatchResult:
        hits: list[KeywordHit] = []
        generic: set[str] = {rule.keyword for rule in self._rules if rule.needs_context}
        for keyword, pattern, ignore in self._patterns:  # every keyword is checked
            ignored = [m.span() for p in ignore for m in p.finditer(text)]
            found = [
                m for m in pattern.finditer(text)
                if not any(start <= m.start() and m.end() <= end for start, end in ignored)
            ]
            if found:
                first = found[0]
                hits.append(KeywordHit(keyword=keyword, count=len(found), snippet=_snippet(text, first.start(), first.end())))
        if any(hit.keyword in generic for hit in hits):  # generic hits need the client's context
            has_specific = any(hit.keyword not in generic for hit in hits)
            if not has_specific and not any(p.search(text) for p in self._context):
                hits = [hit for hit in hits if hit.keyword not in generic]
        return MatchResult(hits=hits)
