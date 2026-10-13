"""The one extra guard exception: exact, developer-approved, read-only POST paths per site."""

import pytest

from sonar.navigation.guards import is_allowed
from sonar.sites import SiteConfig, load_sites


def test_a_read_post_path_lets_exactly_that_post_through_and_nothing_else() -> None:
    hosts = {"a.example.com"}
    paths = frozenset({"/app/list-async.si"})
    assert is_allowed("POST", "https://a.example.com/app/list-async.si?x=1", False, hosts, read_post_paths=paths)
    assert not is_allowed("POST", "https://a.example.com/app/submit-bid.si", False, hosts, read_post_paths=paths)  # other path
    assert not is_allowed("POST", "https://a.example.com/app/list-async.si.evil", False, hosts, read_post_paths=paths)  # not a prefix match
    assert not is_allowed("POST", "https://evil.example.org/app/list-async.si", False, hosts, read_post_paths=paths)  # other host
    assert not is_allowed("PUT", "https://a.example.com/app/list-async.si", False, hosts, read_post_paths=paths)  # only POST
    assert not is_allowed("DELETE", "https://a.example.com/app/list-async.si", False, hosts, read_post_paths=paths)
    assert not is_allowed("POST", "https://a.example.com/app/list-async.si", False, hosts)  # nothing approved by default


def test_the_login_window_still_works_and_is_separate() -> None:
    hosts = {"a.example.com"}
    assert is_allowed("POST", "https://a.example.com/login", True, hosts, login_post_ok=True)
    assert not is_allowed("POST", "https://a.example.com/login", True, hosts, login_post_ok=False)


def test_read_post_paths_must_be_exact_paths() -> None:
    ok = SiteConfig(name="x", start_url="https://a.example.com/", allowed_hosts=["a.example.com"], read_post_paths=["/app/list.si"])
    assert ok.read_post_paths == ["/app/list.si"]
    for bad in ["app/list.si", "/app/*", "https://a.example.com/app/list.si", "/app/list.si?x=1"]:
        with pytest.raises(ValueError, match="exact paths"):
            SiteConfig(name="x", start_url="https://a.example.com/", allowed_hosts=["a.example.com"], read_post_paths=[bad])


def test_only_ontario_has_an_approved_path_and_it_is_the_list_refresh() -> None:
    sites = {s.name: s for s in load_sites()}
    assert sites["canadabuys"].read_post_paths == []
    assert sites["ontariotenders"].read_post_paths == ["/esop/toolkit/opportunity/current/list-async.si"]
