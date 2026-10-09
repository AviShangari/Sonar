"""Runs a matcher over stored listings that have no decision from it yet, and stores the decisions.

Safe to repeat: a listing that already has a decision from this matcher is skipped, so a run
that crashed between storing and matching is finished by the next run.
"""

import logging

from sqlalchemy.engine import Engine

from sonar.db.queries import add_decision, site_posting_texts, undecided_listings
from sonar.extraction.boilerplate import find_boilerplate, strip_boilerplate
from sonar.matching.base import Matcher
from sonar.models import Decision

log = logging.getLogger("sonar.matching")


def warn_keywords_in_boilerplate(site: str, texts: list[str], boilerplate: set[str], matcher: Matcher) -> list[str]:
    """Log (and return) a warning for every stripped line that contains a client keyword, so a
    human can confirm the line really is boilerplate and no keyword is silently being lost."""
    warnings = []
    for line in sorted(boilerplate):
        hits = matcher.match(line).hits
        if not hits:
            continue
        in_postings = sum(line in {raw.strip() for raw in text.splitlines()} for text in texts)
        warnings.append(
            f"site={site} keywords={[h.keyword for h in hits]} appear in a stripped boilerplate line found in "
            f"{in_postings}/{len(texts)} postings: {line[:160]!r}"
        )
    for warning in warnings:
        log.warning(warning)
    return warnings


def match_new_listings(engine: Engine, matcher: Matcher, matcher_name: str, site: str, run_id: int) -> list[Decision]:
    """Match each undecided listing of a site on its cleaned text (site-wide boilerplate removed;
    the stored text stays raw). Returns the decisions made."""
    listings = undecided_listings(engine, site, matcher_name)
    if not listings:
        return []
    texts = site_posting_texts(engine, site)
    boilerplate = find_boilerplate(texts)
    warn_keywords_in_boilerplate(site, texts, boilerplate, matcher)
    decisions = []
    for listing in listings:
        result = matcher.match(strip_boilerplate(listing.posting_text, boilerplate))
        decision = Decision(
            listing_id=listing.id, run_id=run_id, matcher=matcher_name, matched=result.matched,
            details=result.model_dump(),
        )
        add_decision(engine, decision)
        decisions.append(decision)
    return decisions
