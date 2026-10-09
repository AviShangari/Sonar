"""One run of the pipeline: for each site, navigate, dedup, extract, store; then match.

Sites fail independently: an error on one site is logged and recorded, and the others still run.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.engine import Engine

from sonar.config import Settings
from sonar.db.queries import add_listing, listing_exists, newest_listed_date
from sonar.extraction.posting import extract_listing
from sonar.matching.base import Matcher
from sonar.matching.run import match_new_listings
from sonar.models import Decision
from sonar.navigation.factory import build_navigator
from sonar.navigation.session import open_browser
from sonar.sites import SiteConfig

log = logging.getLogger("sonar.pipeline")


@dataclass
class SiteOutcome:
    site: str
    error: str | None = None  # None: the site worked
    stored: int = 0
    skipped_seen: int = 0
    failed_postings: int = 0
    decisions: list[Decision] | None = None

    @property
    def matches(self) -> int:
        return sum(1 for d in self.decisions or [] if d.matched)


async def collect_site(
    site: SiteConfig, settings: Settings, engine: Engine, run_id: int, days: int | None = None, max_new: int | None = None
) -> SiteOutcome:
    """Navigate one site and store its new postings. `max_new` is a test cap, not for real runs."""
    outcome = SiteOutcome(site.name)
    cutoff = newest_listed_date(engine, site.name) or date.today() - timedelta(days=days if days is not None else site.first_run_days)
    async with open_browser(site, port=settings.cdp_port, headless=settings.headless) as context:
        navigator = build_navigator(
            settings.navigator, site, context, settings.cdp_port,
            is_seen=lambda job_id: listing_exists(engine, site.name, job_id), cutoff=cutoff,
        )
        await navigator.login()
        while (max_new is None or outcome.stored < max_new) and (opened := await navigator.next_listing()):
            try:
                add_listing(engine, await extract_listing(site.name, opened), run_id)
                outcome.stored += 1
                log.info("site=%s job_id=%s step=store outcome=ok", site.name, opened.job_id)
            except Exception as exc:  # one bad posting must not stop the site
                outcome.failed_postings += 1
                log.warning("site=%s url=%s step=store outcome=failed error=%s", site.name, opened.url, exc)
            finally:
                await navigator.close_listing(opened)
        outcome.skipped_seen = getattr(navigator, "skipped_seen", 0)
    return outcome


async def run_site(
    site: SiteConfig, settings: Settings, engine: Engine, matcher: Matcher, run_id: int,
    days: int | None = None, max_new: int | None = None,
) -> SiteOutcome:
    """Collect, then match everything of this site that has no decision yet. Never raises."""
    try:
        outcome = await collect_site(site, settings, engine, run_id, days, max_new)
        outcome.decisions = match_new_listings(engine, matcher, settings.matcher, site.name, run_id)
    except Exception as exc:
        log.exception("site=%s step=run outcome=failed", site.name)
        return SiteOutcome(site.name, error=f"{type(exc).__name__}: {exc}")
    return outcome
