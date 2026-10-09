"""Generic posting extraction, done in code (never by an agent): the page's text, HTML and title."""

import hashlib

from sonar.models import Listing
from sonar.navigation.base import OpenedListing

TEXT_SELECTORS = ("main", "[role=main]", "body")  # first one with text wins


async def extract_listing(site: str, opened: OpenedListing) -> Listing:
    """Read the opened tab into a Listing. Raises ValueError if there is no job ID or no text."""
    if opened.job_id is None:
        raise ValueError("no job ID in the URL; the title + client + date fallback is not built yet")
    page = opened.page
    text = ""
    for selector in TEXT_SELECTORS:
        element = await page.query_selector(selector)
        if element:
            text = (await element.inner_text()).strip()
            if text:
                break
    if not text:
        raise ValueError("posting has no text")
    return Listing(
        site=site,
        job_id=opened.job_id,
        url=opened.url,
        title=(await page.title()).strip() or None,
        posting_text=text,
        raw_html=await page.content(),
        fields={"listed_date": opened.listed_date.isoformat() if opened.listed_date else None},
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
