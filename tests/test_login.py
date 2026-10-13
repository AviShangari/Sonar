"""Login by our code: form finding, the guard's login window, success and failure.
A tiny local web server plays the site; a real headless browser runs through the real guards."""

import asyncio
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import pytest
from playwright.async_api import async_playwright

from sonar.navigation import login
from sonar.navigation.guards import GuardState, install_guards, is_allowed
from sonar.navigation.login import LoginFailed, log_in
from sonar.sites import SiteConfig

USER, PASSWORD = "demo@example.com", "s3cret-pass"  # fake test values, not real credentials

# Not Jaggaer-shaped on purpose: different field names, a decoy search box before the form,
# a hidden field, and a submit button without type="submit" being a plain <button>.
LOGIN_PAGE = """<html><body>
<input type="text" name="q" placeholder="search the site">
<form method="post" action="/do-login">
  <input type="hidden" name="token" value="abc">
  <label>Email <input type="email" name="email"></label>
  <label>Secret <input type="password" name="pw"></label>
  <button>Sign in</button>
</form></body></html>"""
HOME_PAGE = "<html><body><h1>Dashboard</h1><a href='/list'>Opportunities</a></body></html>"


class FakeSite(BaseHTTPRequestHandler):
    posts: list[str] = []

    def do_GET(self) -> None:
        body = HOME_PAGE if self.path == "/home" else LOGIN_PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_POST(self) -> None:
        form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        FakeSite.posts.append(self.path)
        ok = form.get("email") == [USER] and form.get("pw") == [PASSWORD]
        self.send_response(303 if ok else 200)
        if ok:
            self.send_header("Location", "/home")
            self.end_headers()
            return
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(LOGIN_PAGE.encode())  # wrong credentials: the same form again

    def log_message(self, *args) -> None:
        pass


@pytest.fixture()
def server():
    FakeSite.posts = []
    httpd = HTTPServer(("127.0.0.1", 0), FakeSite)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@asynccontextmanager
async def guarded_context(guard: GuardState):
    blocked: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        await install_guards(context, ["127.0.0.1"], blocked, guard)
        try:
            yield context, blocked
        finally:
            await browser.close()


def site_for(base: str) -> SiteConfig:
    return SiteConfig(name="demo", start_url=base + "/home", allowed_hosts=["127.0.0.1"], requires_login=True, login_url=base + "/login")


def with_credentials(monkeypatch, password: str = PASSWORD) -> None:
    monkeypatch.setattr(login, "site_credentials", lambda name: (USER, password))


def test_guard_rule_allows_a_post_only_inside_the_login_window_and_only_to_allowed_hosts() -> None:
    hosts = {"a.example.com"}
    assert not is_allowed("POST", "https://a.example.com/login", True, hosts)  # closed by default
    assert is_allowed("POST", "https://a.example.com/login", True, hosts, login_post_ok=True)
    assert not is_allowed("POST", "https://evil.example.org/steal", True, hosts, login_post_ok=True)  # never elsewhere
    assert not is_allowed("PUT", "https://evil.example.org/x", False, hosts, login_post_ok=True)
    assert is_allowed("PUT", "https://a.example.com/x", False, hosts, login_post_ok=True)  # inside the window, own host only


def test_the_window_lets_a_limited_number_of_posts_through_and_closes() -> None:
    guard = GuardState()
    assert not guard.login_open
    guard.open_login_window()
    assert guard.login_open and guard.posts_left == 3
    guard.close_login_window()
    assert not guard.login_open and guard.posts_left == 0


def test_a_login_with_the_right_credentials_succeeds_and_the_window_is_shut_again(server, monkeypatch) -> None:
    with_credentials(monkeypatch)

    async def scenario() -> None:
        guard = GuardState()
        async with guarded_context(guard) as (context, blocked):
            await log_in(context, site_for(server), guard)
            assert not guard.login_open  # shut straight after the submission
            page = context.pages[-1]
            assert page.url.endswith("/home") and "Dashboard" in await page.inner_text("body")
            assert not blocked
            # Outside the window every POST is blocked again, whoever sends it.
            result = await page.evaluate("fetch('/do-login', {method: 'POST', body: 'x=1'}).then(r => r.status, e => 'blocked')")
            assert result == "blocked"
            assert any(b.startswith("POST") for b in blocked)

    asyncio.run(scenario())
    assert FakeSite.posts == ["/do-login"]  # exactly one real submission reached the site


def test_wrong_credentials_fail_once_without_retrying_and_without_leaking_the_password(server, monkeypatch) -> None:
    with_credentials(monkeypatch, password="not-the-password")

    async def scenario() -> str:
        guard = GuardState()
        async with guarded_context(guard) as (context, _):
            with pytest.raises(LoginFailed) as caught:
                await log_in(context, site_for(server), guard)
            assert not guard.login_open
            return str(caught.value)

    message = asyncio.run(scenario())
    assert "demo" in message and "not-the-password" not in message and USER not in message
    assert FakeSite.posts == ["/do-login"]  # tried exactly once: retrying could lock an account


def test_the_form_is_found_by_shape_not_by_names(server) -> None:
    async def scenario() -> tuple[str | None, str | None]:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(LOGIN_PAGE)
            user_box, password_box, submit = await login.find_form(page)
            found = (await user_box.get_attribute("name"), await password_box.get_attribute("name"))
            assert submit is not None and "Sign in" in await submit.inner_text()
            await browser.close()
            return found

    assert asyncio.run(scenario()) == ("email", "pw")  # not the decoy search box, not the hidden field


def test_a_page_without_a_password_box_is_already_logged_in(server, monkeypatch) -> None:
    with_credentials(monkeypatch)
    site = site_for(server).model_copy(update={"login_url": server + "/home"})

    async def scenario() -> None:
        guard = GuardState()
        async with guarded_context(guard) as (context, _):
            await log_in(context, site, guard)  # nothing to fill in, nothing to submit

    asyncio.run(scenario())
    assert FakeSite.posts == []


def test_a_site_without_login_is_left_alone(monkeypatch) -> None:
    monkeypatch.setattr(login, "site_credentials", lambda name: pytest.fail("credentials must not be read"))
    site = SiteConfig(name="demo", start_url="https://x.example.com/", allowed_hosts=["x.example.com"])
    asyncio.run(log_in(None, site, GuardState()))  # type: ignore[arg-type]


def test_a_login_site_needs_a_login_url() -> None:
    with pytest.raises(ValueError, match="login_url"):
        SiteConfig(name="demo", start_url="https://x.example.com/", allowed_hosts=["x.example.com"], requires_login=True)


def test_credentials_come_from_site_named_variables(monkeypatch) -> None:
    from sonar import secrets

    monkeypatch.setenv("SONAR_ONTARIOTENDERS_USERNAME", "u")
    monkeypatch.setenv("SONAR_ONTARIOTENDERS_PASSWORD", "p")
    assert secrets.site_credentials("ontariotenders") == ("u", "p")
    monkeypatch.delenv("SONAR_ONTARIOTENDERS_PASSWORD")
    monkeypatch.setattr(secrets, "load_dotenv", lambda *a, **k: None)  # do not read the real .env
    with pytest.raises(RuntimeError, match="SONAR_ONTARIOTENDERS_PASSWORD"):
        secrets.site_credentials("ontariotenders")
