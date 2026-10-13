import asyncio

import pytest

from sonar.navigation.guards import is_allowed
from sonar.navigation.session import open_browser
from sonar.navigation.urls import job_id_from_url, normalize_url
from sonar.sites import load_sites

HOSTS = {"canadabuys.canada.ca"}


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "post"])
def test_guard_blocks_every_write_method(method: str) -> None:
    assert not is_allowed(method, "https://canadabuys.canada.ca/en/x", False, HOSTS)
    assert not is_allowed(method, "https://canadabuys.canada.ca/en/x", True, HOSTS)


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_guard_allows_read_methods(method: str) -> None:
    assert is_allowed(method, "https://canadabuys.canada.ca/en/x", True, HOSTS)


def test_guard_limits_page_navigation_to_allowed_hosts() -> None:
    assert not is_allowed("GET", "https://evil.example.com/", True, HOSTS)
    assert not is_allowed("GET", "https://canadabuys.canada.ca.evil.com/", True, HOSTS)
    assert not is_allowed("GET", "file:///C:/secret.txt", True, HOSTS)
    assert not is_allowed("GET", "javascript:alert(1)", True, HOSTS)


def test_guard_lets_subresources_load_from_any_host() -> None:
    # Images, scripts and CSS (e.g. from a CDN) are not page navigations.
    assert is_allowed("GET", "https://cdn.example.com/app.js", False, HOSTS)


def test_normalize_url_strips_query_fragment_and_resolves_relative_links() -> None:
    base = "https://CanadaBuys.canada.ca/en/tender-opportunities?status=1"
    assert normalize_url("/en/tender-notice/ws123/?lang=fr#top", base) == (
        "https://canadabuys.canada.ca/en/tender-notice/ws123"
    )
    assert normalize_url("https://a.com/x/") == "https://a.com/x"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://canadabuys.canada.ca/en/tender-notice/ws1234567", "ws1234567"),
        ("https://site.com/bids/view?id=991&lang=en", "991"),
        ("https://site.com/bids/view?NoticeID=A-5", "A-5"),
        ("https://canadabuys.canada.ca/en/tender-opportunities", None),
        ("https://site.com/", None),
        ("https://ontariotenders.app.jaggaer.com/esop/toolkit/opportunity/current/120400/detail.si", "120400"),
        ("https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/cb-428-37324676", "cb-428-37324676"),
    ],
)
def test_job_id_from_url(url: str, expected: str | None) -> None:
    assert job_id_from_url(url) == expected


def test_hosts_can_carry_their_own_hints() -> None:
    from sonar.sites import SiteConfig

    site = SiteConfig(
        name="x", start_url="https://a.example.com/",
        allowed_hosts=["a.example.com", {"host": "b.example.com", "hint": "the list is under Tenders"}],
        hint="whole-site tip",
    )
    assert site.allowed_hosts == ["a.example.com", "b.example.com"]  # the guard still gets plain host names
    assert site.host_hints == {"b.example.com": "the list is under Tenders"}
    text = site.hints_text()
    assert "whole-site tip" in text and "About b.example.com: the list is under Tenders" in text
    assert "trust the page" in text  # hints can go stale


def test_no_hints_means_no_hint_text() -> None:
    from sonar.sites import SiteConfig

    assert SiteConfig(name="x", start_url="https://a.example.com/", allowed_hosts=["a.example.com"]).hints_text() == ""


def test_a_hint_for_a_host_that_is_not_allowed_is_rejected() -> None:
    from sonar.sites import SiteConfig

    with pytest.raises(ValueError, match="not allowed"):
        SiteConfig(name="x", start_url="https://a.example.com/", allowed_hosts=["a.example.com"], host_hints={"evil.example.org": "x"})


def test_the_real_sites_file_loads() -> None:
    from sonar.sites import load_sites

    assert {s.name for s in load_sites()} >= {"canadabuys", "ontariotenders"}


def test_sites_config_loads() -> None:
    site = load_sites()[0]
    assert site.name == "canadabuys"
    assert site.allowed_hosts == ["canadabuys.canada.ca"]
    assert site.requires_login is False


def test_headless_browser_does_not_announce_itself_as_headless() -> None:
    # CanadaBuys answers 403 to a "HeadlessChrome" user agent.
    async def scenario() -> str:
        async with open_browser(load_sites()[0], port=9444, headless=True) as context:
            page = await context.new_page()
            return str(await page.evaluate("navigator.userAgent"))

    agent = asyncio.run(scenario())
    assert "Chrome/" in agent
    assert "Headless" not in agent


def test_timezone_setting_defaults_to_toronto_and_today_follows_it() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from sonar.config import Settings

    assert Settings().timezone == "America/Toronto"
    assert Settings(timezone="Pacific/Kiritimati").today() == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()  # UTC+14


def test_an_unknown_timezone_is_rejected_at_startup() -> None:
    import pytest
    from pydantic import ValidationError

    from sonar.config import Settings

    with pytest.raises(ValidationError):
        Settings(timezone="Mars/Olympus")
