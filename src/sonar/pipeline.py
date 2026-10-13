"""One run of the pipeline: for each site, navigate, dedup, extract, store; then match.

Sites fail independently: an error on one site is logged and recorded, and the others still run.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.engine import Engine

from sonar import budget
from sonar.budget import BudgetExceeded
from sonar.config import Settings
from sonar.db.queries import (
    add_listing,
    advance_watermark,
    get_watermark,
    listing_exists,
    newest_listed_date,
)
from sonar.extraction.posting import extract_listing
from sonar.matching.base import Matcher
from sonar.matching.run import match_new_listings
from sonar.models import Decision
from sonar.navigation.factory import build_navigator
from sonar.navigation.screenshots import FailureShots
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
    # The stop point is the watermark, which only a clean run moves. A capped or failed run leaves it
    # where it was, so the next run re-reads down to it (known postings are skipped by dedup).
    cutoff = get_watermark(engine, site.name) or settings.today() - timedelta(days=days if days is not None else site.first_run_days)
    async with open_browser(site, port=settings.cdp_port, headless=settings.headless) as context:
        shots = FailureShots(settings.screenshot_path, site.name, tz=settings.tz)
        navigator = build_navigator(
            settings.navigator, site, context, settings.cdp_port,
            is_seen=lambda job_id: listing_exists(engine, site.name, job_id), cutoff=cutoff, shots=shots,
        )
        site_cap = budget.current.max_new_per_site
        cap_hit = False  # stopped by a cost limit (not by the test cap): the run is then partial
        try:
            await navigator.login()
            while (max_new is None or outcome.stored < max_new) and (opened := await navigator.next_listing()):
                if site_cap is not None and outcome.stored >= site_cap:
                    cap_hit = True  # an opened posting is waiting; close it unread and stop this site
                    budget.current.note_hit(f"posts:{site.name}", f"{site.name}: limit of {site_cap} new postings reached")
                    await navigator.close_listing(opened)
                    break
                try:
                    add_listing(engine, await extract_listing(site.name, opened), run_id)
                    outcome.stored += 1
                    log.info("site=%s job_id=%s step=store outcome=ok", site.name, opened.job_id)
                except Exception as exc:  # one bad posting must not stop the site
                    outcome.failed_postings += 1
                    log.warning("site=%s url=%s step=store outcome=failed error=%s", site.name, opened.url, exc)
                    await shots.take(opened.page, "store", opened.job_id)
                finally:
                    await navigator.close_listing(opened)
        except BudgetExceeded:
            cap_hit = True  # a run-wide limit ended this site early; it was noted where it was hit
        except Exception:
            # The site is failing as a whole (login, list pages): keep a picture of where the browser was.
            await shots.take_current(context, "site")
            raise
        outcome.skipped_seen = getattr(navigator, "skipped_seen", 0)
        capped = max_new is not None and outcome.stored >= max_new
        failed_opens = getattr(navigator, "failed_opens", 0)
        reached_cutoff = getattr(navigator, "reached_cutoff", True)
        if capped or cap_hit or outcome.failed_postings or failed_opens or not reached_cutoff:
            log.warning(
                "site=%s step=watermark outcome=not_advanced capped=%s cost_limit=%s failed_postings=%d failed_opens=%d reached_cutoff=%s",
                site.name, capped, cap_hit, outcome.failed_postings, failed_opens, reached_cutoff,
            )
        elif (newest := newest_listed_date(engine, site.name)) and advance_watermark(engine, site.name, newest):
            log.info("site=%s step=watermark outcome=advanced to=%s", site.name, newest)
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
