"""Opening a posting by clicking its title when its address is refused in a new tab (HTTP 403).
A tiny local site plays Ontario's behaviour: the posting only opens when it was reached from the list."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from playwright.async_api import async_playwright

from sonar.navigation.dom import DomNavigator
from sonar.sites import SiteConfig

TITLES = ["Alpha tender", "Same title", "Same title"]  # the last two are identical on purpose


class Site(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/list":
            links = "".join(f'<tr><td><a href="/p/{i}">{t}</a></td></tr>' for i, t in enumerate(TITLES))
            self._send(200, f"<html><body><table>{links}</table></body></html>")
        elif self.path.startswith("/p/"):
            if "/list" not in (self.headers.get("Referer") or ""):
                self._send(403, "<html><body>Error!</body></html>")  # typed straight into a new tab
            else:
                i = int(self.path.split("/")[-1])
                self._send(200, f"<html><body><h1>{TITLES[i]}</h1><p>posting number {i}</p></body></html>")
        else:
            self._send(404, "")

    def _send(self, code: int, body: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args) -> None:
        pass


@pytest.fixture()
def base():
    httpd = HTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def run_scenario(base: str, which: list[int]):
    """Open the postings `which` (row numbers) with a real browser. Returns (job_id, page text, address, tabs open) per posting."""
    site = SiteConfig(name="demo", start_url=base + "/list", allowed_hosts=["127.0.0.1"])
    rows = [{"title": TITLES[i], "url": f"{base}/p/{i}", "date": "2026-10-09"} for i in range(len(TITLES))]

    async def collect(site, offset):
        return json.dumps({"links": rows, "has_more": False})

    async def scenario():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            list_page = await context.new_page()
            await list_page.goto(base + "/list")  # the agent leaves the list open in a tab
            nav = DomNavigator(site, context, collect, is_seen=lambda j: False, cutoff=__import__("datetime").date(2026, 10, 1))
            seen = []
            while (opened := await nav.next_listing()):
                text = " ".join((await opened.page.inner_text("body")).split())
                seen.append((opened.job_id, text, opened.in_list_tab, len(context.pages)))
                await nav.close_listing(opened)
                assert list_page.url.endswith("/list") and not list_page.is_closed()  # the list is back, never closed
            await browser.close()
            return seen, nav.failed_opens

    return asyncio.run(scenario())


def test_a_refused_address_is_opened_by_clicking_its_title_and_the_list_tab_survives(base) -> None:
    seen, failed = run_scenario(base, [0, 1, 2])
    assert failed == 0
    assert [s[2] for s in seen] == [True, True, True]  # all were opened by click
    assert "posting number 0" in seen[0][1]
    # Two rows share a title: the second one must open the SECOND link, not the first twice.
    assert "posting number 1" in seen[1][1] and "posting number 2" in seen[2][1]
    assert all(s[3] == 1 for s in seen)  # only the list tab is open: the refused new tab was closed, nothing leaks


def test_a_site_where_nothing_could_be_opened_is_a_failed_site_not_ok(monkeypatch) -> None:
    """Found postings, opened none: the run must not report 'ok' with zero stored."""
    from contextlib import asynccontextmanager
    from datetime import date

    from sonar import pipeline
    from sonar.config import Settings
    from sonar.db import queries
    from sonar.db.tables import make_engine

    class Nav:
        failed_opens, skipped_seen, reached_cutoff = 2, 0, True

        async def login(self) -> None:
            pass

        async def next_listing(self):
            return None

        async def close_listing(self, listing) -> None:
            pass

    @asynccontextmanager
    async def browser(site, port, headless, guard=None, blocked=None):
        yield object()

    monkeypatch.setattr(pipeline, "open_browser", browser)
    monkeypatch.setattr(pipeline, "build_navigator", lambda *a, **k: Nav())
    site = SiteConfig(name="demo", start_url="https://x.example.com/", allowed_hosts=["x.example.com"])
    engine = make_engine("sqlite://")
    outcome = asyncio.run(pipeline.run_site(site, Settings(), engine, matcher=None, run_id=queries.start_run(engine)))  # type: ignore[arg-type]
    assert outcome.error and "none could be opened" in outcome.error


def test_five_postings_that_stay_unreadable_after_the_click_stop_the_site(base, monkeypatch) -> None:
    from datetime import date

    from sonar.navigation.dom import OpenFailures

    class Refusing(Site):  # refuses every posting, whether or not it was clicked
        def do_GET(self) -> None:
            if self.path.startswith("/p/"):
                self._send(403, "<html><body>Error!</body></html>")
            elif self.path == "/list":
                links = "".join(f'<p><a href="/p/{i}">Tender {i}</a></p>' for i in range(8))
                self._send(200, f"<html><body>{links}</body></html>")
            else:
                super().do_GET()

    httpd = HTTPServer(("127.0.0.1", 0), Refusing)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host = f"http://127.0.0.1:{httpd.server_port}"
    rows = [{"title": f"Tender {i}", "url": f"{host}/p/{i}", "date": "2026-10-09"} for i in range(8)]
    site = SiteConfig(name="demo", start_url=host + "/list", allowed_hosts=["127.0.0.1"])

    async def collect(site, offset):
        return json.dumps({"links": rows, "has_more": False})

    async def scenario():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            list_page = await context.new_page()
            await list_page.goto(host + "/list")  # the list IS open, so the click is tried, and refused too
            nav = DomNavigator(site, context, collect, is_seen=lambda j: False, cutoff=date(2026, 10, 1))
            with pytest.raises(OpenFailures, match="5 postings"):
                while await nav.next_listing():
                    pass
            assert nav.failed_opens == 5  # stopped at the fifth, did not try the other three
            await browser.close()

    asyncio.run(scenario())
    httpd.shutdown()


def test_when_no_open_tab_shows_the_list_the_site_stops_at_the_first_failure(base) -> None:
    from datetime import date

    from sonar.navigation.dom import OpenFailures

    rows = [{"title": TITLES[i], "url": f"{base}/p/{i}", "date": "2026-10-09"} for i in range(3)]
    site = SiteConfig(name="demo", start_url=base + "/list", allowed_hosts=["127.0.0.1"])

    async def collect(site, offset):
        return json.dumps({"links": rows, "has_more": False})

    async def scenario():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()  # no list tab open: the agent left the browser elsewhere
            nav = DomNavigator(site, context, collect, is_seen=lambda j: False, cutoff=date(2026, 10, 1))
            with pytest.raises(OpenFailures, match="no open tab shows the list"):
                await nav.next_listing()
            assert nav.failed_opens == 1  # not five
            await browser.close()

    asyncio.run(scenario())


def test_a_list_inside_an_embedded_frame_is_clicked_too(base) -> None:
    """Ariba's Events table is in an iframe, and its links reload the whole tab (target=_parent). The click
    search must look inside frames; a posting address typed into a new tab is refused (403) here."""
    from datetime import date

    class Framed(Site):
        def do_GET(self) -> None:
            if self.path == "/list":
                self._send(200, '<html><body><h1>Dashboard</h1><iframe src="/frame"></iframe></body></html>')
            elif self.path == "/frame":
                links = "".join(f'<p><a target="_parent" href="/hop?itemID=ID{i}">{t}</a></p>' for i, t in enumerate(TITLES))
                self._send(200, f"<html><body>{links}</body></html>")
            elif self.path.startswith("/hop?"):
                if "/frame" not in (self.headers.get("Referer") or ""):
                    self._send(403, "<html><body>Error!</body></html>")
                else:
                    i = int(self.path.split("ID")[-1])
                    self._send(200, f"<html><body><h1>{TITLES[i]}</h1><p>posting number {i}</p></body></html>")
            else:
                self._send(404, "")

    httpd = HTTPServer(("127.0.0.1", 0), Framed)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host = f"http://127.0.0.1:{httpd.server_port}"
    rows = [{"title": TITLES[i], "url": f"{host}/hop?itemID=ID{i}", "date": "2026-10-09"} for i in range(len(TITLES))]
    site = SiteConfig(name="demo", start_url=host + "/list", allowed_hosts=["127.0.0.1"])

    async def collect(site, offset):
        return json.dumps({"links": rows, "has_more": False})

    async def scenario():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            list_page = await context.new_page()
            await list_page.goto(host + "/list")
            await list_page.wait_for_load_state("networkidle")
            nav = DomNavigator(site, context, collect, is_seen=lambda j: False, cutoff=date(2026, 10, 1))
            seen = []
            while (opened := await nav.next_listing()):
                seen.append((opened.job_id, " ".join((await opened.page.inner_text("body")).split()), opened.in_list_tab))
                await nav.close_listing(opened)
                await list_page.wait_for_load_state("networkidle")
            await browser.close()
            return seen, nav.failed_opens

    seen, failed = asyncio.run(scenario())
    httpd.shutdown()
    assert failed == 0
    assert [s[0] for s in seen] == ["ID0", "ID1", "ID2"]  # the ID comes from the kept itemID parameter
    assert all(s[2] for s in seen)  # each was opened by clicking inside the frame
    assert ["posting number 0" in seen[0][1], "posting number 1" in seen[1][1], "posting number 2" in seen[2][1]] == [True] * 3


def test_the_agent_prompt_teaches_clicking_when_typed_addresses_fail() -> None:
    from sonar.navigation.dom import SYSTEM_PROMPT

    assert "never type an address again" in SYSTEM_PROMPT and "click its menu link again" in SYSTEM_PROMPT


def test_after_one_refused_address_the_rest_are_opened_by_clicking_first(base) -> None:
    """The first posting costs a refused address; the next ones must not open any new tab at all."""
    from datetime import date

    attempts: list[str] = []

    class Counting(Site):
        def do_GET(self) -> None:
            if self.path.startswith("/p/") and "/list" not in (self.headers.get("Referer") or ""):
                attempts.append(self.path)  # a posting address typed into a tab (no Referer from the list)
            super().do_GET()

    httpd = HTTPServer(("127.0.0.1", 0), Counting)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host = f"http://127.0.0.1:{httpd.server_port}"
    rows = [{"title": TITLES[i], "url": f"{host}/p/{i}", "date": "2026-10-09"} for i in range(3)]
    site = SiteConfig(name="demo", start_url=host + "/list", allowed_hosts=["127.0.0.1"])

    async def collect(site, offset):
        return json.dumps({"links": rows, "has_more": False})

    async def scenario():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            await (await context.new_page()).goto(host + "/list")
            nav = DomNavigator(site, context, collect, is_seen=lambda j: False, cutoff=date(2026, 10, 1))
            texts = []
            while (opened := await nav.next_listing()):
                texts.append(" ".join((await opened.page.inner_text("body")).split()))
                await nav.close_listing(opened)
            assert nav.prefer_click and nav.failed_opens == 0
            await browser.close()
            return texts

    texts = asyncio.run(scenario())
    httpd.shutdown()
    assert [t.split("posting number ")[1][:1] for t in texts] == ["0", "1", "2"]  # all three read, the right ones
    assert attempts == ["/p/0"]  # only the FIRST posting tried the address; the other two went straight to clicking


def test_rows_that_all_carry_the_list_pages_own_address_stay_separate_and_get_their_ids_from_the_click(base) -> None:
    """The agent sometimes returns the list page's address for every row (no posting ID in it). The rows must
    not collapse into one, and each posting's ID must come from the address the browser is on after the click."""
    from datetime import date

    rows = [{"title": TITLES[i], "url": f"{base}/list", "date": "2026-10-09"} for i in range(3)]
    site = SiteConfig(name="demo", start_url=base + "/list", allowed_hosts=["127.0.0.1"])

    async def collect(site, offset):
        return json.dumps({"links": rows, "has_more": False})

    async def scenario(seen_ids: set[str]):
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            await (await context.new_page()).goto(base + "/list")
            nav = DomNavigator(site, context, collect, is_seen=lambda j: j in seen_ids, cutoff=date(2026, 10, 1))
            out = []
            while (opened := await nav.next_listing()):
                out.append((opened.job_id, opened.url.rsplit("/", 1)[-1]))
                await nav.close_listing(opened)
            await browser.close()
            return out, nav.skipped_seen, nav.failed_opens

    out, skipped, failed = asyncio.run(scenario(set()))
    # "/list" typed in a new tab loads fine and does not show the title, so each row falls back to its click
    assert [o[0] for o in out] == ["0", "1", "2"] and failed == 0  # three rows, three IDs read after the click
    out, skipped, _ = asyncio.run(scenario({"1"}))  # posting 1 is already stored: found out only after opening
    assert [o[0] for o in out] == ["0", "2"] and skipped == 1
