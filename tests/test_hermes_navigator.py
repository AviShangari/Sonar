import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from sonar.config import HermesSettings
from sonar.hermes_runner import HermesError
from sonar.navigation import factory
from sonar.navigation.dom import CollectionError, DomNavigator, parse_agent_page
from sonar.navigation.hermes import HermesCollector, first_task, next_task, parse_row_block
from sonar.navigation.urls import job_id_from_url, normalize_url
from sonar.sites import SiteConfig

SITE = SiteConfig(name="demo", start_url="https://bids.example.com/list", allowed_hosts=["bids.example.com"], max_list_pages=4)
LOGIN_SITE = SiteConfig(
    name="portal", start_url="https://portal.example.com/home", allowed_hosts=["portal.example.com"],
    requires_login=True, login_url="https://portal.example.com/login", hint="Use the Sourcing tab.",
)
HOSTS = {"bids.example.com"}
CUTOFF = date(2026, 10, 8)


def block(*lines: str) -> str:
    return "\n".join(lines)


def test_good_rows_are_taken_in_order() -> None:
    rows, rejected = parse_row_block(block(
        "ROW|First tender|https://bids.example.com/n/1|2026-10-09",
        "ROW|Second tender|https://bids.example.com/n/2|null",
    ), HOSTS)
    assert rejected == 0
    assert rows == [
        {"title": "First tender", "url": "https://bids.example.com/n/1", "date": "2026-10-09"},
        {"title": "Second tender", "url": "https://bids.example.com/n/2", "date": None},
    ]


@pytest.mark.parametrize("line", [
    "not a row",
    "ROW|only|three",
    "ROW|t|http://bids.example.com/n/1|2026-10-09",  # not https
    "ROW|t|https://evil.example.org/n/1|2026-10-09",  # host not allowed
    "ROW|t|https://bids.example.com/n/1|09/10/2026",  # date format
    "ROW||https://bids.example.com/n/1|2026-10-09",  # no title
    "ROW|t|javascript:alert(1)|2026-10-09",
])
def test_bad_lines_are_rejected_and_counted(line: str) -> None:
    assert parse_row_block(line, HOSTS) == ([], 1)


def test_a_title_may_contain_a_pipe_and_repeated_addresses_are_dropped() -> None:
    rows, rejected = parse_row_block(block(
        "ROW|Tender | part 2|https://bids.example.com/n/1|2026-10-09",
        "ROW|Same address|https://bids.example.com/n/1|2026-10-09",
    ), HOSTS)
    assert [r["title"] for r in rows] == ["Tender | part 2"] and rejected == 1


def test_ontarios_rfqid_addresses_stay_distinct() -> None:
    a = "https://ontariotenders.app.jaggaer.com/esop/toolkit/rfq/initDetailRfq.do?rfqId=111&x=1"
    b = "https://ontariotenders.app.jaggaer.com/esop/toolkit/rfq/initDetailRfq.do?rfqId=222&x=1"
    assert normalize_url(a) != normalize_url(b)
    assert job_id_from_url(a) == "111" and job_id_from_url(b) == "222"


class FakeServer:
    """Stands in for HermesServer: plays back (printed block, final answer) pairs and records the messages."""

    instances: list["FakeServer"] = []

    def __init__(self, cfg, port, site_name, steps: list[tuple[str, dict]] | None = None) -> None:
        self.capture_path = Path(cfg.home) / "capture.txt"
        self.steps = list(steps if steps is not None else FakeServer.script)
        self.messages: list[str] = []
        self.started = self.stopped = False
        FakeServer.instances.append(self)

    script: list[tuple[str, dict]] = []

    async def start(self) -> None:
        self.started = True

    async def ask(self, message: str) -> str:
        self.messages.append(message)
        printed, answer = self.steps.pop(0)
        self.capture_path.parent.mkdir(parents=True, exist_ok=True)
        if printed:
            self.capture_path.write_text(printed, encoding="utf-8")
        return "Done.\n" + json.dumps(answer)

    async def stop(self) -> None:
        self.stopped = True


class FakeContext:
    pages: list = []


@pytest.fixture
def cfg(tmp_path) -> HermesSettings:
    FakeServer.instances = []
    FakeServer.script = []
    return HermesSettings(home=str(tmp_path))


def collector(cfg) -> HermesCollector:
    return HermesCollector(cfg, 9333, FakeContext(), server_factory=FakeServer)  # type: ignore[arg-type]


def test_first_step_returns_rows_in_the_navigators_format(cfg) -> None:
    FakeServer.script = [(
        block("ROW|A|https://bids.example.com/n/1|2026-10-09", "ROW|B|https://bids.example.com/n/2|2026-10-08"),
        {"rows_from": "last_extraction", "has_more": True, "route_note": "Clicked Open tenders.", "stale_note": None},
    )]
    raw = asyncio.run(collector(cfg)(SITE, 0))
    page = parse_agent_page(raw)  # the navigator's own parser accepts it
    assert [link.url for link in page.links] == ["https://bids.example.com/n/1", "https://bids.example.com/n/2"]
    assert page.has_more is True and page.route_note == "Clicked Open tenders."


def test_a_stale_note_from_hermes_reaches_the_route_note(cfg) -> None:
    FakeServer.script = [(
        "ROW|A|https://bids.example.com/n/1|2026-10-09",
        {"rows_from": "last_extraction", "has_more": False, "route_note": None, "stale_note": "The list moved to /en/tenders."},
    )]
    c = collector(cfg)
    page = parse_agent_page(asyncio.run(c(SITE, 0)))
    assert "out of date" in (page.route_note or "") and "/en/tenders" in (page.route_note or "")
    assert c.stale_note == "The list moved to /en/tenders."


def test_a_followup_message_is_sent_to_the_same_server_for_the_next_page(cfg) -> None:
    FakeServer.script = [
        ("ROW|A|https://bids.example.com/n/1|2026-10-09", {"rows_from": "last_extraction", "has_more": True}),
        ("ROW|B|https://bids.example.com/n/2|2026-10-08", {"rows_from": "last_extraction", "has_more": False}),
    ]
    c = collector(cfg)
    asyncio.run(c(SITE, 0))
    page = parse_agent_page(asyncio.run(c(SITE, 1)))
    assert len(FakeServer.instances) == 1  # one server, one conversation
    assert "already collected" in FakeServer.instances[0].messages[1] and "1 rows" in FakeServer.instances[0].messages[1]
    assert [link.url for link in page.links] == ["https://bids.example.com/n/2"]
    assert page.route_note is None


def test_a_block_from_an_earlier_step_is_never_read_twice(cfg) -> None:
    FakeServer.script = [
        ("ROW|A|https://bids.example.com/n/1|2026-10-09", {"rows_from": "last_extraction", "has_more": True}),
        ("", {"rows_from": "last_extraction", "has_more": False}),  # Hermes printed nothing this step
    ]
    c = collector(cfg)
    asyncio.run(c(SITE, 0))
    assert parse_agent_page(asyncio.run(c(SITE, 1))).links == []


def test_rows_typed_into_the_answer_are_checked_like_printed_ones(cfg) -> None:
    FakeServer.script = [("", {"rows": [
        {"title": "A", "url": "https://bids.example.com/n/1", "date": "2026-10-09"},
        {"title": "Bad", "url": "https://evil.example.org/x", "date": "2026-10-09"},
    ], "has_more": False})]
    page = parse_agent_page(asyncio.run(collector(cfg)(SITE, 0)))
    assert [link.url for link in page.links] == ["https://bids.example.com/n/1"]


def test_an_answer_that_is_not_json_is_a_collection_error(cfg) -> None:
    class Chatty(FakeServer):
        async def ask(self, message: str) -> str:
            return "I could not find the list."

    c = HermesCollector(cfg, 9333, FakeContext(), server_factory=Chatty)  # type: ignore[arg-type]
    with pytest.raises(CollectionError):
        asyncio.run(c(SITE, 0))


def test_a_hermes_failure_becomes_a_site_failure_with_the_reason(cfg) -> None:
    class Failing(FakeServer):
        async def start(self) -> None:
            raise HermesError("preflight refused to start Hermes: the guard answered wrongly")

    c = HermesCollector(cfg, 9333, FakeContext(), server_factory=Failing)  # type: ignore[arg-type]
    with pytest.raises(CollectionError, match="preflight refused"):
        asyncio.run(c(SITE, 0))
    asyncio.run(c.aclose())  # nothing was started, nothing to stop


class FakeTab:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_the_last_tab_is_never_closed_but_a_stray_blank_one_is(cfg) -> None:
    """Closing the browser's only tab closes the whole window (and Hermes's connection to it): CanadaBuys has no login,
    so its only tab is a blank one."""
    FakeServer.script = [("ROW|A|https://bids.example.com/n/1|2026-10-09", {"rows_from": "last_extraction"})] * 2

    only_blank = FakeContext()
    only_blank.pages = [FakeTab("about:blank")]
    asyncio.run(HermesCollector(cfg, 9333, only_blank, server_factory=FakeServer)(SITE, 0))  # type: ignore[arg-type]
    assert not only_blank.pages[0].closed

    with_page = FakeContext()
    with_page.pages = [FakeTab("about:blank"), FakeTab("https://portal.example.com/home")]
    asyncio.run(HermesCollector(cfg, 9333, with_page, server_factory=FakeServer)(SITE, 0))  # type: ignore[arg-type]
    assert [t.closed for t in with_page.pages] == [True, False]


def test_aclose_stops_the_server_once(cfg) -> None:
    FakeServer.script = [("ROW|A|https://bids.example.com/n/1|2026-10-09", {"rows_from": "last_extraction"})]
    c = collector(cfg)
    asyncio.run(c(SITE, 0))
    asyncio.run(c.aclose())
    asyncio.run(c.aclose())
    assert FakeServer.instances[0].stopped


def test_the_first_task_carries_the_start_address_hints_and_no_secrets() -> None:
    task = first_task(LOGIN_SITE, learn=True)
    assert "https://portal.example.com/home" in task and "Use the Sourcing tab." in task
    assert "already logged in" in task and "never log in" in task
    assert "skill_manage" in task and "BEGIN_ROWS" in task
    assert "password" not in first_task(SITE, learn=False).replace("passwords", "").lower()
    assert "skill_manage" not in first_task(SITE, learn=False)


def test_the_followup_task_names_the_offset() -> None:
    assert "12 rows were already collected" in next_task(12)


class FakeListContext:
    pages: list = []

    async def new_page(self):
        raise AssertionError("no posting should be opened in this test")


def test_the_navigator_reads_the_list_through_hermes_and_releases_it(cfg) -> None:
    """DomNavigator with the Hermes collector: rows older than the cutoff end collection, known IDs are skipped,
    and the server is stopped as soon as the list is read, before any posting is opened."""
    FakeServer.script = [(
        block("ROW|Seen|https://bids.example.com/n/1|2026-10-09", "ROW|Old|https://bids.example.com/n/2|2026-10-01"),
        {"rows_from": "last_extraction", "has_more": True, "route_note": None},
    )]
    c = collector(cfg)
    nav = DomNavigator(SITE, FakeListContext(), c, is_seen=lambda job_id: job_id == "1", cutoff=CUTOFF)  # type: ignore[arg-type]

    async def scenario() -> None:
        assert await nav.next_listing() is None
        assert nav.skipped_seen == 1 and nav.reached_cutoff
        assert FakeServer.instances[0].stopped

    asyncio.run(scenario())


def test_the_server_is_stopped_even_when_collection_fails(cfg) -> None:
    FakeServer.script = [("", {"rows_from": "last_extraction", "has_more": False})]
    c = collector(cfg)
    nav = DomNavigator(SITE, FakeListContext(), c, is_seen=lambda job_id: False, cutoff=CUTOFF)  # type: ignore[arg-type]
    with pytest.raises(CollectionError):
        asyncio.run(nav.next_listing())
    assert FakeServer.instances[0].stopped


def test_the_factory_builds_the_hermes_navigator(monkeypatch, cfg) -> None:
    monkeypatch.setattr(factory, "load_hermes_settings", lambda: cfg)
    nav = factory.build_navigator("hermes", SITE, FakeListContext(), 9333, lambda job_id: False, CUTOFF)  # type: ignore[arg-type]
    assert isinstance(nav, DomNavigator) and isinstance(nav.collect, HermesCollector)


class FakeFrame:
    def __init__(self, anchors: list[tuple[str, str, str]]) -> None:
        self.anchors = anchors

    async def evaluate(self, js: str) -> list[list[str]]:
        return [list(a) for a in self.anchors]


class FakeListTab:
    url = "https://bids.example.com/list"

    def __init__(self, anchors: list[tuple[str, str, str]]) -> None:
        self.frames = [FakeFrame(anchors)]


LIST = "https://bids.example.com/list"


def test_a_row_with_the_list_pages_address_gets_its_own_link_from_the_open_list_tab(cfg) -> None:
    """Hermes printed the list page's address for rows whose link it could not read (CanadaBuys, run 2): the navigator then
    saw several 'postings' with no ID at one address and could not open them."""
    FakeServer.script = [(
        block(f"ROW|Caretaking Supplies|{LIST}|2026-10-27", f"ROW|Cut off title that is long enough to be shortened|{LIST}|2026-10-27",
              "ROW|Fine row|https://bids.example.com/n/7|2026-10-27", f"ROW|Not on the page|{LIST}|2026-10-27"),
        {"rows_from": "last_extraction", "has_more": False},
    )]
    context = FakeContext()
    context.pages = [FakeListTab([
        ("Caretaking Supplies", "", "https://bids.example.com/n/sk-srpsd20270002"),
        ("Cut off title that is long enough to be shortened…", "", "https://bids.example.com/n/sk-hwy-260005"),
        ("Fine row", "", "https://bids.example.com/n/7"),
        ("Menu", "", LIST),
    ])]
    page = parse_agent_page(asyncio.run(HermesCollector(cfg, 9333, context, server_factory=FakeServer)(SITE_WITH_LIST, 0)))  # type: ignore[arg-type]
    assert [link.url for link in page.links] == [
        "https://bids.example.com/n/sk-srpsd20270002", "https://bids.example.com/n/sk-hwy-260005", "https://bids.example.com/n/7",
    ]  # the row that is nowhere on the page is dropped, not opened


def test_rows_with_their_own_addresses_never_touch_the_page(cfg) -> None:
    FakeServer.script = [("ROW|A|https://bids.example.com/n/1|2026-10-09", {"rows_from": "last_extraction"})]

    reads: list[int] = []

    class CountingFrame(FakeFrame):
        async def evaluate(self, js: str) -> list[list[str]]:
            reads.append(1)
            return []

    tab = FakeListTab([])
    tab.frames = [CountingFrame([])]
    context = FakeContext()
    context.pages = [tab]
    page = parse_agent_page(asyncio.run(HermesCollector(cfg, 9333, context, server_factory=FakeServer)(SITE_WITH_LIST, 0)))  # type: ignore[arg-type]
    assert len(page.links) == 1 and reads == []  # the list tab is not read when every row has its own address


def test_several_rows_may_carry_the_list_pages_address_but_other_repeats_are_still_dropped() -> None:
    rows, rejected = parse_row_block(block(
        f"ROW|One|{LIST}|2026-10-27", f"ROW|Two|{LIST}|2026-10-27",
        "ROW|Three|https://bids.example.com/n/3|2026-10-27", "ROW|Three again|https://bids.example.com/n/3|2026-10-27",
    ), HOSTS, normalize_url(LIST))
    assert [r["title"] for r in rows] == ["One", "Two", "Three"] and rejected == 1


SITE_WITH_LIST = SiteConfig(name="demo", start_url=f"{LIST}?sort=desc&per=50", allowed_hosts=["bids.example.com"], max_list_pages=4)


def test_a_row_without_a_job_id_in_its_address_is_logged_with_that_address(cfg, caplog) -> None:
    FakeServer.script = [("ROW|Odd one|https://bids.example.com/en/notices|2026-10-27", {"rows_from": "last_extraction"})]
    with caplog.at_level("WARNING", logger="sonar.hermes"):
        asyncio.run(HermesCollector(cfg, 9333, FakeContext(), server_factory=FakeServer)(SITE_WITH_LIST, 0))  # type: ignore[arg-type]
    assert "no_job_id" in caplog.text and "https://bids.example.com/en/notices" in caplog.text


def test_the_task_asks_for_the_title_alone_and_merx_has_a_pause() -> None:
    from sonar.sites import load_sites

    assert "title only, without its category, buyer, location or dates" in first_task(SITE, learn=False)
    assert "title only, without its category, buyer, location or dates" in next_task(25)
    assert {s.name: s.open_delay_seconds for s in load_sites()}["merx"] == 1.5
