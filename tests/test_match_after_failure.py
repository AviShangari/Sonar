"""A site that fails part-way still gets its stored postings matched and reported.

Before this, `run_site` skipped matching whenever collection raised, so postings stored before the
failure stayed unscored and were missing from the report (and, if the failure repeated, forever)."""

import asyncio
from contextlib import asynccontextmanager
from datetime import date

import pytest

from sonar import pipeline
from sonar.config import Settings
from sonar.db import queries
from sonar.db.tables import make_engine
from sonar.main import print_summary, run_status
from sonar.matching.keywords import KeywordMatcher
from sonar.models import Listing
from sonar.navigation.base import OpenedListing
from sonar.sites import SiteConfig

SITE = SiteConfig(name="demo", start_url="https://bids.example.com/", allowed_hosts=["bids.example.com"], first_run_days=3)


class Page:
    async def close(self) -> None:
        pass


class BreakingNavigator:
    """Hands out `postings`, then raises like a navigator whose site stopped working."""

    skipped_seen, failed_opens, reached_cutoff = 0, 0, True

    def __init__(self, postings: list[tuple[str, str]], then: Exception | None = RuntimeError("site went away")) -> None:
        self._queue = list(postings)
        self._then = then

    async def login(self) -> None:
        pass

    async def next_listing(self) -> OpenedListing | None:
        if not self._queue:
            if self._then:
                raise self._then
            return None
        job_id, _ = self._queue.pop(0)
        return OpenedListing(job_id=job_id, url=f"https://bids.example.com/{job_id}", page=Page(), listed_date=date(2026, 10, 9))

    async def close_listing(self, listing: OpenedListing) -> None:
        pass


@pytest.fixture()
def engine():
    return make_engine("sqlite://")


@pytest.fixture()
def wire(monkeypatch):
    state: dict = {"navigator": BreakingNavigator([])}

    @asynccontextmanager
    async def fake_browser(site, port, headless, guard=None, blocked=None):
        yield object()

    async def fake_extract(site_name: str, opened: OpenedListing) -> Listing:
        body = "We need SAP help" if opened.job_id.startswith("sap") else "Plain tender"
        return Listing(
            site=site_name, job_id=opened.job_id, url=opened.url, title="t", posting_text=body,
            text_hash=opened.job_id, fields={"listed_date": "2026-10-09"},
        )

    async def fake_shot(self, *args, **kwargs):
        return None

    monkeypatch.setattr(pipeline, "open_browser", fake_browser)
    monkeypatch.setattr(pipeline, "build_navigator", lambda *a, **k: state["navigator"])
    monkeypatch.setattr(pipeline, "extract_listing", fake_extract)
    monkeypatch.setattr(pipeline.FailureShots, "take_current", fake_shot)
    return state


def run(engine, run_id: int):
    return asyncio.run(pipeline.run_site(SITE, Settings(), engine, KeywordMatcher(["SAP"]), run_id))


def test_postings_stored_before_a_site_failure_are_matched_and_the_failure_is_still_reported(engine, wire) -> None:
    run_id = queries.start_run(engine)
    wire["navigator"] = BreakingNavigator([("sap1", ""), ("plain1", "")])
    outcome = run(engine, run_id)
    assert outcome.error and "site went away" in outcome.error  # still a failed site
    assert outcome.stored == 2 and len(outcome.decisions) == 2 and outcome.matches == 1
    assert [m[0].job_id for m in queries.matched_listings(engine, run_id)] == ["sap1"]  # reaches the report
    assert queries.get_watermark(engine, "demo") is None  # a failed run still must not move the stop point


def test_a_site_that_fails_every_time_still_scores_what_earlier_runs_left_unscored(engine, wire) -> None:
    """The recurring-failure case: the old postings were stored but never scored, because every run failed."""
    first = queries.start_run(engine)
    queries.add_listing(engine, Listing(site="demo", job_id="sap-old", url="https://x/1", posting_text="SAP project", text_hash="1"), first)
    wire["navigator"] = BreakingNavigator([])  # fails at once, stores nothing new
    second = queries.start_run(engine)
    outcome = run(engine, second)
    assert outcome.error and outcome.stored == 0
    assert [m[0].job_id for m in queries.matched_listings(engine, second)] == ["sap-old"]


def test_a_clean_site_is_unchanged(engine, wire) -> None:
    run_id = queries.start_run(engine)
    wire["navigator"] = BreakingNavigator([("sap1", "")], then=None)
    outcome = run(engine, run_id)
    assert outcome.error is None and outcome.stored == 1 and outcome.matches == 1


def test_a_matching_failure_keeps_the_collection_result_and_names_both_problems(engine, wire) -> None:
    class BrokenMatcher:
        def match(self, text: str):
            raise ValueError("matcher broke")

    run_id = queries.start_run(engine)
    wire["navigator"] = BreakingNavigator([("sap1", "")], then=None)
    outcome = asyncio.run(pipeline.run_site(SITE, Settings(), engine, BrokenMatcher(), run_id))  # type: ignore[arg-type]
    assert outcome.stored == 1 and outcome.decisions is None
    assert outcome.error == "matching failed: ValueError: matcher broke"

    wire["navigator"] = BreakingNavigator([("sap2", "")])
    outcome = asyncio.run(pipeline.run_site(SITE, Settings(), engine, BrokenMatcher(), queries.start_run(engine)))  # type: ignore[arg-type]
    assert "site went away" in outcome.error and "matching failed" in outcome.error


def test_a_failed_site_with_matches_is_a_partial_run_and_the_summary_shows_its_counts(engine, wire, capsys) -> None:
    run_id = queries.start_run(engine)
    wire["navigator"] = BreakingNavigator([("sap1", "")])
    bad = run(engine, run_id)
    good = pipeline.SiteOutcome("other", decisions=[])
    assert run_status([bad, good]) == "partial"
    print_summary(run_id, "partial", [bad, good])
    out = capsys.readouterr().out
    assert "demo: FAILED" in out and "1 new stored" in out and "1 matches" in out
