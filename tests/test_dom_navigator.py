import asyncio
import json
from datetime import date

import pytest

from sonar.navigation.code_check import check_code
from sonar.navigation.dom import READ_ROWS_RECIPE, CollectionError, DomNavigator, parse_agent_page, parse_date, to_candidates
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
    def __init__(self) -> None:
        self.closed = False

    async def goto(self, url: str, wait_until: str) -> object:
        return type("Response", (), {"status": 200})()

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


def test_list_not_sorted_newest_first_is_an_error() -> None:
    async def scenario() -> None:
        nav, _ = make_navigator([answer([row("a1", "2026/10/08"), row("b1", "2026/10/09")])])
        with pytest.raises(CollectionError, match="sorted"):
            await nav.next_listing()

    asyncio.run(scenario())


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
