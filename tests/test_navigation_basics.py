import pytest

from sonar.navigation.guards import is_allowed
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
    ],
)
def test_job_id_from_url(url: str, expected: str | None) -> None:
    assert job_id_from_url(url) == expected


def test_sites_config_loads() -> None:
    (site,) = load_sites()
    assert site.name == "canadabuys"
    assert site.allowed_hosts == ["canadabuys.canada.ca"]
    assert site.requires_login is False
