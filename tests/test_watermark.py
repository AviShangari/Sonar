"""The stop point (watermark) moves only after a clean collection, so a capped or failed run
cannot cause older, unseen postings to be skipped on the next run."""

import asyncio
from contextlib import asynccontextmanager
from datetime import date, timedelta

import pytest

from sonar import pipeline
from sonar.config import Settings
from sonar.db import queries
from sonar.db.tables import make_engine
from sonar.models import Listing
from sonar.navigation.base import OpenedListing
from sonar.sites import SiteConfig

SITE = SiteConfig(name="demo", start_url="https://bids.example.com/", allowed_hosts=["bids.example.com"], first_run_days=3)


class FakePage:
    async def close(self) -> None:
        pass


class FakeNavigator:
    """Hands out postings (job_id, listed_date) newest first, with the flags a real navigator exposes."""

    def __init__(self, postings: list[tuple[str, str]], failed_opens: int = 0, reached_cutoff: bool = True) -> None:
        self._queue = list(postings)
        self.skipped_seen = 0
        self.failed_opens = failed_opens
        self.reached_cutoff = reached_cutoff

    async def login(self) -> None:
        pass

    async def next_listing(self) -> OpenedListing | None:
        if not self._queue:
            return None
        job_id, listed = self._queue.pop(0)
        return OpenedListing(job_id=job_id, url=f"https://bids.example.com/{job_id}", page=FakePage(), listed_date=date.fromisoformat(listed))

    async def close_listing(self, listing: OpenedListing) -> None:
        pass


@pytest.fixture()
def engine():
    return make_engine("sqlite://")


@pytest.fixture()
def wire(monkeypatch, engine):
    """Replace the browser, navigator and extraction. Returns a function to set the navigator and
    a list that records the cutoff each run was given."""
    state: dict = {"navigator": FakeNavigator([]), "cutoffs": [], "fail_ids": set()}

    @asynccontextmanager
    async def fake_browser(site, port, headless, guard=None, blocked=None):
        yield object()

    def fake_build_navigator(mode, site, context, port, is_seen, cutoff, shots=None, guard=None):
        state["cutoffs"].append(cutoff)
        return state["navigator"]

    async def fake_extract(site_name: str, opened: OpenedListing) -> Listing:
        if opened.job_id in state["fail_ids"]:
            raise RuntimeError("extraction failed")
        return Listing(
            site=site_name, job_id=opened.job_id, url=opened.url, title="t", posting_text="text",
            text_hash=opened.job_id, fields={"listed_date": opened.listed_date.isoformat()},
        )

    monkeypatch.setattr(pipeline, "open_browser", fake_browser)
    monkeypatch.setattr(pipeline, "build_navigator", fake_build_navigator)
    monkeypatch.setattr(pipeline, "extract_listing", fake_extract)
    return state


def collect(engine, max_new: int | None = None):
    run_id = queries.start_run(engine)
    return asyncio.run(pipeline.collect_site(SITE, Settings(), engine, run_id, max_new=max_new))


def test_a_clean_run_advances_the_watermark_to_the_newest_stored_date(engine, wire) -> None:
    wire["navigator"] = FakeNavigator([("a", "2026-10-09"), ("b", "2026-10-08")])
    collect(engine)
    assert queries.get_watermark(engine, "demo") == date(2026, 10, 9)


def test_a_capped_run_leaves_the_watermark_so_older_postings_are_not_skipped(engine, wire) -> None:
    wire["navigator"] = FakeNavigator([("a", "2026-10-09"), ("b", "2026-10-05"), ("c", "2026-10-04")])
    collect(engine, max_new=1)  # stores only the newest posting
    assert queries.get_watermark(engine, "demo") is None
    # The next run starts from the first-run window again, not from 10-09, so b and c are still reachable.
    wire["navigator"] = FakeNavigator([("a", "2026-10-09"), ("b", "2026-10-05"), ("c", "2026-10-04")])
    collect(engine)
    assert wire["cutoffs"][1] == Settings().today() - timedelta(days=3)
    assert all(queries.listing_exists(engine, "demo", j) for j in "abc")


def test_a_cap_that_was_not_reached_still_counts_as_clean(engine, wire) -> None:
    wire["navigator"] = FakeNavigator([("a", "2026-10-09")])
    collect(engine, max_new=5)  # only 1 posting existed, so the cap never cut anything off
    assert queries.get_watermark(engine, "demo") == date(2026, 10, 9)


def test_a_posting_that_failed_to_store_blocks_the_watermark(engine, wire) -> None:
    wire["fail_ids"] = {"b"}
    wire["navigator"] = FakeNavigator([("a", "2026-10-09"), ("b", "2026-10-08")])
    outcome = collect(engine)
    assert outcome.failed_postings == 1
    assert queries.get_watermark(engine, "demo") is None


def test_a_posting_that_failed_to_open_blocks_the_watermark(engine, wire) -> None:
    wire["navigator"] = FakeNavigator([("a", "2026-10-09")], failed_opens=1)
    collect(engine)
    assert queries.get_watermark(engine, "demo") is None


def test_hitting_the_page_cap_blocks_the_watermark(engine, wire) -> None:
    wire["navigator"] = FakeNavigator([("a", "2026-10-09")], reached_cutoff=False)
    collect(engine)
    assert queries.get_watermark(engine, "demo") is None


def test_a_crash_midway_blocks_the_watermark_and_the_next_run_starts_from_the_old_one(engine, wire) -> None:
    queries.advance_watermark(engine, "demo", date(2026, 10, 1))

    class CrashingNavigator(FakeNavigator):
        async def next_listing(self) -> OpenedListing | None:
            if not self._queue:
                raise RuntimeError("browser died")
            return await super().next_listing()

    wire["navigator"] = CrashingNavigator([("a", "2026-10-09")])
    with pytest.raises(RuntimeError):
        collect(engine)
    assert queries.get_watermark(engine, "demo") == date(2026, 10, 1)  # unchanged
    wire["navigator"] = FakeNavigator([])
    collect(engine)
    assert wire["cutoffs"][-1] == date(2026, 10, 1)


def test_the_next_run_uses_the_watermark_as_its_cutoff(engine, wire) -> None:
    wire["navigator"] = FakeNavigator([("a", "2026-10-09")])
    collect(engine)
    wire["navigator"] = FakeNavigator([])
    collect(engine)
    assert wire["cutoffs"][1] == date(2026, 10, 9)
