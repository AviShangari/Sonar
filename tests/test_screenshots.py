"""A screenshot is saved when navigation fails, and taking one can never make a failure worse."""

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from sonar import pipeline
from sonar.config import Settings
from sonar.db import queries
from sonar.db.tables import make_engine
from sonar.models import Listing
from sonar.navigation.base import OpenedListing
from sonar.navigation.screenshots import FailureShots
from sonar.sites import SiteConfig

SITE = SiteConfig(name="demo", start_url="https://bids.example.com/", allowed_hosts=["bids.example.com"], first_run_days=3)


class FakePage:
    """Stands in for a Playwright page: 'takes' a screenshot by writing a few bytes."""

    def __init__(self, url: str = "https://bids.example.com/x", closed: bool = False, broken: bool = False) -> None:
        self.url = url
        self.closed = closed
        self.broken = broken

    def is_closed(self) -> bool:
        return self.closed

    async def screenshot(self, path: str, timeout: int) -> None:
        if self.broken:
            raise TimeoutError("page is hung")
        Path(path).write_bytes(b"png")

    async def close(self) -> None:
        pass


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages


def run(coro):
    return asyncio.run(coro)


def test_a_real_browser_page_is_saved_as_a_png(tmp_path) -> None:
    async def go() -> Path | None:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content("<h1>Access denied</h1>")
                return await FailureShots(tmp_path, "demo").take(page, "open", "cb-1")
            finally:
                await browser.close()

    path = run(go())
    assert path and path.parent == tmp_path and path.read_bytes().startswith(b"\x89PNG")
    assert "demo-open-cb-1" in path.name


def test_the_file_name_cannot_escape_the_screenshot_folder(tmp_path) -> None:
    path = run(FailureShots(tmp_path, "demo").take(FakePage(), "open", "../../evil/..\\x"))
    assert path and path.parent == tmp_path and path.exists()


def test_two_failures_in_the_same_second_do_not_overwrite_each_other(tmp_path) -> None:
    shots = FailureShots(tmp_path, "demo")
    first = run(shots.take(FakePage(), "open", "a"))
    second = run(shots.take(FakePage(), "open", "a"))
    assert first != second and len(list(tmp_path.iterdir())) == 2


def test_a_closed_page_is_skipped_quietly(tmp_path) -> None:
    assert run(FailureShots(tmp_path, "demo").take(FakePage(closed=True), "open")) is None
    assert not list(tmp_path.glob("*"))


def test_a_screenshot_error_is_swallowed(tmp_path) -> None:
    assert run(FailureShots(tmp_path, "demo").take(FakePage(broken=True), "open")) is None


def test_it_stops_at_the_limit_so_the_disk_cannot_fill(tmp_path) -> None:
    shots = FailureShots(tmp_path, "demo", limit=2)
    results = [run(shots.take(FakePage(), "open", str(i))) for i in range(4)]
    assert [r is not None for r in results] == [True, True, False, False]


def test_take_current_picks_the_last_real_page(tmp_path) -> None:
    context = FakeContext([FakePage("https://bids.example.com/list"), FakePage("about:blank"), FakePage(closed=True)])
    path = run(FailureShots(tmp_path, "demo").take_current(context, "site"))
    assert path and path.exists()


def test_take_current_with_no_real_page_does_nothing(tmp_path) -> None:
    assert run(FailureShots(tmp_path, "demo").take_current(FakeContext([FakePage("about:blank")]), "site")) is None
    assert run(FailureShots(tmp_path, "demo").take_current(object(), "site")) is None  # no .pages at all


# ---- wired into the pipeline ----


class OnePostingNavigator:
    def __init__(self, crash: bool = False) -> None:
        self.crash = crash
        self.sent = False

    async def login(self) -> None:
        pass

    async def next_listing(self) -> OpenedListing | None:
        if self.crash:
            raise RuntimeError("agent could not read the list")
        if self.sent:
            return None
        self.sent = True
        return OpenedListing(job_id="a", url="https://bids.example.com/a", page=FakePage(), listed_date=date(2026, 10, 9))

    async def close_listing(self, listing: OpenedListing) -> None:
        pass


@pytest.fixture()
def wire(monkeypatch):
    state: dict = {"navigator": OnePostingNavigator(), "extract_fails": False}

    @asynccontextmanager
    async def fake_browser(site, port, headless):
        yield FakeContext([FakePage("https://bids.example.com/list")])

    def fake_build_navigator(mode, site, context, port, is_seen, cutoff, shots=None):
        return state["navigator"]

    async def fake_extract(site_name: str, opened: OpenedListing) -> Listing:
        if state["extract_fails"]:
            raise RuntimeError("no posting text")
        return Listing(site=site_name, job_id=opened.job_id, url=opened.url, title="t", posting_text="x", text_hash="h", fields={})

    monkeypatch.setattr(pipeline, "open_browser", fake_browser)
    monkeypatch.setattr(pipeline, "build_navigator", fake_build_navigator)
    monkeypatch.setattr(pipeline, "extract_listing", fake_extract)
    return state


def collect(tmp_path):
    engine = make_engine("sqlite://")
    settings = Settings(screenshot_dir=str(tmp_path))
    return asyncio.run(pipeline.collect_site(SITE, settings, engine, queries.start_run(engine)))


def test_a_posting_that_fails_to_store_leaves_a_screenshot(tmp_path, wire) -> None:
    wire["extract_fails"] = True
    outcome = collect(tmp_path)
    assert outcome.failed_postings == 1
    assert [p.name.split("-")[2:4] for p in tmp_path.glob("*.png")] == [["demo", "store"]]


def test_a_crash_while_collecting_leaves_a_screenshot_and_still_raises(tmp_path, wire) -> None:
    wire["navigator"] = OnePostingNavigator(crash=True)
    with pytest.raises(RuntimeError, match="could not read the list"):
        collect(tmp_path)
    assert len(list(tmp_path.glob("*-site-*.png"))) == 1


def test_a_clean_run_takes_no_screenshots(tmp_path, wire) -> None:
    collect(tmp_path)
    assert not list(tmp_path.glob("*"))


def test_file_names_use_the_given_time_zone(tmp_path) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    toronto = ZoneInfo("America/Toronto")
    path = run(FailureShots(tmp_path, "demo", tz=toronto).take(FakePage(), "open"))
    stamp = datetime.strptime(path.name[:15], "%Y%m%d-%H%M%S")
    assert abs((stamp - datetime.now(toronto).replace(tzinfo=None)).total_seconds()) < 60
