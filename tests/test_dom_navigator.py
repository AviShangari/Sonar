import asyncio
import json
from datetime import date

import pytest

from sonar.navigation.code_check import check_code
from sonar.navigation.dom import READ_ROWS_RECIPE, CollectionError, DomNavigator, parse_agent_page, parse_date, to_candidates
from sonar.navigation.screenshots import FailureShots
from sonar.sites import SiteConfig

SITE = SiteConfig(
    name="demo",
    start_url="https://bids.example.com/list?page=1",
    allowed_hosts=["bids.example.com"],
    max_list_pages=4,
)
CUTOFF = date(2026, 10, 8)


def answer(rows: list[tuple[str, str | None]], has_more: bool = False) -> str:
    """An agent answer. Each row is (url, date); the answer is wrapped like a chatty model would."""
    links = [{"title": f"T{i}", "url": url, "date": d} for i, (url, d) in enumerate(rows)]
    return "Here you go:\n```json\n" + json.dumps({"links": links, "has_more": has_more}) + "\n```"


def row(job: str, d: str | None = "2026/10/09") -> tuple[str, str | None]:
    return f"https://bids.example.com/n/{job}", d


def test_parse_accepts_json_wrapped_in_text_and_fences() -> None:
    assert len(parse_agent_page(answer([row("a1")])).links) == 1


@pytest.mark.parametrize("bad", ["no json here", '{"links": "nope"}', '{"links": [{"title": "x"}]}', "{broken"])
def test_parse_rejects_bad_answers(bad: str) -> None:
    with pytest.raises(CollectionError):
        parse_agent_page(bad)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("2026/10/09", date(2026, 10, 9)), ("2026-10-09 Amended", date(2026, 10, 9)), ("2026/13/45", None), (None, None), ("soon", None)],
)
def test_parse_date(text: str | None, expected: date | None) -> None:
    assert parse_date(text) == expected


def test_to_candidates_filters_normalizes_and_reads_ids_and_dates() -> None:
    found = parse_agent_page(answer([
        ("https://bids.example.com/n/ws1?lang=en", "2026/10/09"),
        ("/n/ws2/", "2026-10-08"),  # relative link
        ("https://evil.example.org/n/ws3", "2026/10/09"),  # wrong host
        ("javascript:alert(1)", None),  # not http(s)
        ("https://bids.example.com/help", None),  # no ID: kept, ID is None
    ]))
    cands = to_candidates(found, SITE)
    assert [(c.url, c.job_id, c.listed_date) for c in cands] == [
        ("https://bids.example.com/n/ws1", "ws1", date(2026, 10, 9)),
        ("https://bids.example.com/n/ws2", "ws2", date(2026, 10, 8)),
        ("https://bids.example.com/help", None, None),
    ]


class FakePage:
    """A posting page. Its text holds every title the fake agent hands out (T0, T1, ...), so the
    title check passes unless a test says otherwise."""

    text = " ".join(f"T{i}" for i in range(50))

    def __init__(self) -> None:
        self.closed = False

    async def goto(self, url: str, wait_until: str) -> object:
        return type("Response", (), {"status": 200})()

    async def inner_text(self, selector: str, timeout: int) -> str:
        return self.text

    async def title(self) -> str:
        return ""

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self) -> None:
        self.opened: list[FakePage] = []

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.opened.append(page)
        return page


def make_navigator(steps: list[str], seen: set[str] | None = None, context: FakeContext | None = None):
    """A navigator whose agent replies with `steps` in order. `calls` records each offset asked for."""
    calls: list[int] = []

    async def collect(site: SiteConfig, offset: int) -> str:
        calls.append(offset)
        return steps[len(calls) - 1]

    nav = DomNavigator(
        SITE, context or FakeContext(), collect, is_seen=lambda job_id: job_id in (seen or set()), cutoff=CUTOFF  # type: ignore[arg-type]
    )
    return nav, calls


async def drain(nav: DomNavigator) -> list[str | None]:
    ids = []
    while (opened := await nav.next_listing()):
        ids.append(opened.job_id)
        await nav.close_listing(opened)
    return ids


def test_seen_ids_are_skipped_without_opening_a_tab() -> None:
    async def scenario() -> None:
        context = FakeContext()
        nav, _ = make_navigator([answer([row("ws1"), row("ws2")])], seen={"ws1"}, context=context)
        assert await drain(nav) == ["ws2"]
        assert len(context.opened) == 1  # ws1 never opened
        assert nav.skipped_seen == 1

    asyncio.run(scenario())


def test_collection_stops_at_first_row_older_than_the_cutoff() -> None:
    async def scenario() -> None:
        page1 = answer([row("a1", "2026/10/09"), row("b1", "2026/10/08"), row("c1", "2026/10/07"), row("d1", "2026/10/07")], has_more=True)
        nav, calls = make_navigator([page1])
        assert await drain(nav) == ["a1", "b1"]  # same day as cutoff kept; c and d dropped
        assert calls == [0]  # no "load more" once an older row was seen

    asyncio.run(scenario())


def test_load_more_continues_with_the_row_offset_until_old_rows_appear() -> None:
    async def scenario() -> None:
        page1 = answer([row("a1"), row("b1")], has_more=True)
        page2 = answer([row("c1", "2026/10/08"), row("d1", "2026/10/05")], has_more=True)
        nav, calls = make_navigator([page1, page2])
        assert await drain(nav) == ["a1", "b1", "c1"]
        assert calls == [0, 2]  # second step asked for rows after the first 2

    asyncio.run(scenario())


def test_collection_stops_when_there_is_no_more_to_load() -> None:
    async def scenario() -> None:
        nav, calls = make_navigator([answer([row("a1")], has_more=False)])
        assert await drain(nav) == ["a1"]
        assert calls == [0]

    asyncio.run(scenario())


def test_page_cap_limits_load_more_steps() -> None:
    async def scenario() -> None:
        steps = [answer([row(f"p{i}")], has_more=True) for i in range(10)]
        nav, calls = make_navigator(steps)
        await drain(nav)
        assert calls == [0, 1, 2, 3]  # max_list_pages = 4

    asyncio.run(scenario())


def test_reached_cutoff_is_false_only_when_the_page_cap_ends_collection() -> None:
    async def scenario() -> None:
        capped, _ = make_navigator([answer([row(f"p{i}")], has_more=True) for i in range(10)])
        await drain(capped)
        assert capped.reached_cutoff is False

        old_row, _ = make_navigator([answer([row("a1"), row("c1", "2026/10/07")], has_more=True)])
        await drain(old_row)
        assert old_row.reached_cutoff is True

        no_more, _ = make_navigator([answer([row("a1")], has_more=False)])
        await drain(no_more)
        assert no_more.reached_cutoff is True

    asyncio.run(scenario())


def test_a_posting_that_will_not_open_is_counted() -> None:
    async def scenario() -> None:
        class BrokenPage(FakePage):
            async def goto(self, url: str, wait_until: str) -> object:
                return type("Response", (), {"status": 500})()

        class BrokenContext(FakeContext):
            async def new_page(self) -> FakePage:
                return BrokenPage()

        nav, _ = make_navigator([answer([row("a1")])], context=BrokenContext())
        assert await drain(nav) == []
        assert nav.failed_opens == 1

    asyncio.run(scenario())


def test_a_list_not_sorted_newest_first_skips_old_rows_and_reads_every_page() -> None:
    async def scenario() -> None:
        # Sorted by closing date: an old row comes first and must NOT end collection.
        page1 = answer([row("old1", "2026/09/01"), row("new1", "2026/10/09"), row("old2", "2026/08/15")], has_more=True)
        page2 = answer([row("new2", "2026-10-10"), row("old3", "2026/07/01")], has_more=False)
        nav, calls = make_navigator([page1, page2])
        assert await drain(nav) == ["new1", "new2"]
        assert calls == [0, 3]  # kept reading past the old rows
        assert nav.unsorted and nav.reached_cutoff  # the whole list was read, so the run is clean

    asyncio.run(scenario())


def test_an_unsorted_list_cut_by_the_page_cap_is_not_clean() -> None:
    async def scenario() -> None:
        steps = [answer([row(f"p{i}a", "2026/09/01"), row(f"p{i}b", "2026/10/09")], has_more=True) for i in range(10)]
        nav, calls = make_navigator(steps)
        await drain(nav)
        assert len(calls) == 4 and nav.reached_cutoff is False  # max_list_pages = 4

    asyncio.run(scenario())


def test_a_list_whose_rows_are_all_older_than_the_cutoff_is_a_quiet_day_not_a_failure() -> None:
    async def scenario() -> None:
        nav, _ = make_navigator([answer([row("a1", "2026/10/01"), row("b1", "2026/09/30")], has_more=True)])
        assert await drain(nav) == []
        assert nav.reached_cutoff

    asyncio.run(scenario())


def test_a_posting_that_does_not_show_its_row_title_is_a_failed_open() -> None:
    async def scenario() -> None:
        class WrongPage(FakePage):
            text = "Some other tender entirely"

        class WrongContext(FakeContext):
            async def new_page(self) -> FakePage:
                page = WrongPage()
                self.opened.append(page)
                return page

        nav, _ = make_navigator([answer([row("a1")])], context=WrongContext())
        assert await drain(nav) == []  # a wrong address the agent built is never stored
        assert nav.failed_opens == 1

    asyncio.run(scenario())


def test_the_title_check_allows_shortened_titles_and_spacing_differences() -> None:
    from sonar.navigation.dom import shows_title

    class Page(FakePage):
        text = "Project: tender_21955 - IT Security   Products and Services: Next Gen VOR\nDetails..."

    assert asyncio.run(shows_title(Page(), "IT Security Products and Services: Next…"))
    assert asyncio.run(shows_title(Page(), ""))  # nothing to check
    assert not asyncio.run(shows_title(Page(), "Winter Maintenance for the Timmins Area"))


def test_without_dates_collection_stops_at_a_page_of_only_known_postings() -> None:
    async def scenario() -> None:
        page1 = answer([row("a1", None), row("b1", None)], has_more=True)
        page2 = answer([row("c1", None), row("d1", None)], has_more=True)
        page3 = answer([row("e1", None)], has_more=True)
        nav, calls = make_navigator([page1, page2, page3], seen={"c1", "d1"})
        assert await drain(nav) == ["a1", "b1"]  # page 2 is all known, so page 3 is never asked for
        assert calls == [0, 2]

    asyncio.run(scenario())


def test_zero_usable_links_is_a_failure_not_a_quiet_day() -> None:
    async def scenario() -> None:
        nav, _ = make_navigator([answer([("https://evil.example.org/n/ws1", "2026/10/09")])])
        with pytest.raises(CollectionError):
            await nav.next_listing()

    asyncio.run(scenario())


def test_close_listing_closes_the_tab() -> None:
    async def scenario() -> None:
        context = FakeContext()
        nav, _ = make_navigator([answer([row("ws1")])], context=context)
        opened = await nav.next_listing()
        await nav.close_listing(opened)  # type: ignore[arg-type]
        assert context.opened[0].closed

    asyncio.run(scenario())


def test_the_recipe_in_the_agent_prompt_passes_the_code_filter() -> None:
    check_code(READ_ROWS_RECIPE)  # raises CodeRejected if the prompt teaches code we would refuse


def test_a_posting_that_will_not_open_is_screenshotted_then_closed(tmp_path) -> None:
    class BlockedPage(FakePage):
        async def goto(self, url: str, wait_until: str) -> object:
            return type("Response", (), {"status": 403})()

        def is_closed(self) -> bool:
            return self.closed

        async def screenshot(self, path: str, timeout: int) -> None:
            assert not self.closed  # the picture is taken before the tab is closed
            open(path, "wb").write(b"png")

    class BlockedContext(FakeContext):
        async def new_page(self) -> FakePage:
            page = BlockedPage()
            self.opened.append(page)
            return page

    async def scenario() -> None:
        calls: list[int] = []

        async def collect(site: SiteConfig, offset: int) -> str:
            calls.append(offset)
            return answer([row("ws1")])

        context = BlockedContext()
        nav = DomNavigator(
            SITE, context, collect, is_seen=lambda job_id: False, cutoff=CUTOFF, shots=FailureShots(tmp_path, "demo")  # type: ignore[arg-type]
        )
        assert await drain(nav) == []
        assert nav.failed_opens == 1 and context.opened[0].closed
        assert len(list(tmp_path.glob("*-demo-open-ws1-*.png"))) == 1

    asyncio.run(scenario())


def paced_navigator(delay: float, rows: list[str], seen: set[str] | None = None, context: FakeContext | None = None):
    """A navigator for a site with `open_delay_seconds`; `sleeps` records each pause instead of waiting."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def collect(site: SiteConfig, offset: int) -> str:
        return answer([row(job) for job in rows])

    site = SITE.model_copy(update={"open_delay_seconds": delay})
    nav = DomNavigator(
        site, context or FakeContext(), collect, is_seen=lambda job_id: job_id in (seen or set()), cutoff=CUTOFF, sleep=fake_sleep  # type: ignore[arg-type]
    )
    return nav, sleeps


def test_a_pause_comes_before_every_open_except_the_first() -> None:
    async def scenario() -> None:
        nav, sleeps = paced_navigator(10, ["a1", "b1", "c1"])
        assert await drain(nav) == ["a1", "b1", "c1"]
        assert len(sleeps) == 2
        assert all(7 <= s <= 13 for s in sleeps)  # 10 seconds, +-30%

    asyncio.run(scenario())


def test_known_postings_cost_no_pause() -> None:
    async def scenario() -> None:
        nav, sleeps = paced_navigator(10, ["a1", "b1", "c1"], seen={"a1", "b1"})
        assert await drain(nav) == ["c1"]
        assert sleeps == []  # only one posting was really opened

    asyncio.run(scenario())


def test_no_delay_is_the_default() -> None:
    async def scenario() -> None:
        assert SITE.open_delay_seconds == 0
        nav, sleeps = paced_navigator(0, ["a1", "b1"])
        assert await drain(nav) == ["a1", "b1"]
        assert sleeps == []

    asyncio.run(scenario())


@pytest.mark.parametrize("bad", [-1, 500])
def test_the_delay_must_be_sensible(bad: float) -> None:
    with pytest.raises(ValueError):
        SiteConfig(name="x", start_url="https://x.example.com", allowed_hosts=["x.example.com"], open_delay_seconds=bad)


def test_canadabuys_is_paced() -> None:
    from sonar.sites import load_sites

    assert {s.name: s.open_delay_seconds for s in load_sites()}["canadabuys"] > 0


def test_a_posting_is_given_time_to_finish_loading_before_it_is_read() -> None:
    states: list[tuple[str, int]] = []

    class LoadingPage(FakePage):
        async def wait_for_load_state(self, state: str, timeout: int) -> None:
            assert not self.closed
            states.append((state, timeout))

    class LoadingContext(FakeContext):
        async def new_page(self) -> FakePage:
            page = LoadingPage()
            self.opened.append(page)
            return page

    async def scenario() -> None:
        nav, _ = make_navigator([answer([row("a1"), row("b1")])], context=LoadingContext())
        assert await drain(nav) == ["a1", "b1"]
        assert states == [("load", 15_000)] * 2

    asyncio.run(scenario())


def test_a_page_that_never_finishes_loading_is_still_read() -> None:
    class SlowPage(FakePage):
        async def wait_for_load_state(self, state: str, timeout: int) -> None:
            raise TimeoutError("page never settled")

    class SlowContext(FakeContext):
        async def new_page(self) -> FakePage:
            page = SlowPage()
            self.opened.append(page)
            return page

    async def scenario() -> None:
        nav, _ = make_navigator([answer([row("a1")])], context=SlowContext())
        assert await drain(nav) == ["a1"]
        assert nav.failed_opens == 0

    asyncio.run(scenario())
