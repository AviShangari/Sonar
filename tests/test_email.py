import pytest

from sonar.config import Settings
from sonar.models import KeywordHit, Report, ReportMatch
from sonar import secrets
from sonar.report import send
from sonar.report.render import render_email_text


def make_report(status: str = "ok", failed: str | None = None, matches: list[ReportMatch] | None = None) -> Report:
    return Report(run_id=3, status=status, started_at="2026-10-11 09:00 UTC", failed_sites=failed,
                  new_postings=5, scored=5, matcher="keywords", matches=matches or [])


def make_match(summary: str | None = "A short summary.", **extra) -> ReportMatch:
    return ReportMatch(
        title="SAP support", url="https://x.example/a", site="demo", job_id="a", summary=summary, **extra,
        hits=[KeywordHit(keyword="SAP", count=3, snippet="we need SAP help"),
              KeywordHit(keyword="Project Management", count=2, snippet="project management lead"),
              KeywordHit(keyword="ERP", count=1, snippet="an ERP rollout")],
    )


def settings(**kw) -> Settings:
    return Settings(email=True, smtp_host="smtp.example", email_from="a@example.com", email_to="b@example.com", **kw)


def test_email_lists_title_summary_link_and_counts() -> None:
    body = render_email_text(make_report(matches=[make_match()]))
    assert "SAP support" in body and "A short summary." in body and "https://x.example/a" in body
    assert "6 mentions, 3 different keywords" in body
    assert "SAP (3), Project Management (2), ERP (1)" in body
    assert "(+2 more)" in body  # one snippet shown, the other two are counted
    assert "we need SAP help" in body and "project management lead" not in body


def test_email_without_snippets_or_summary() -> None:
    body = render_email_text(make_report(matches=[make_match(summary=None)]), snippets=False)
    assert "we need SAP help" not in body and "more)" not in body and "A short summary." not in body


def test_email_is_produced_with_zero_matches_and_lists_failed_sites() -> None:
    body = render_email_text(make_report(status="partial", failed="siteA: boom"))
    assert "No new postings matched" in body and "siteA: boom" in body


def test_send_uses_starttls_login_and_message(monkeypatch) -> None:
    calls: list = []

    class FakeSMTP:
        def __init__(self, host, port, timeout): calls.append(("connect", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context): calls.append("starttls")
        def login(self, user, password): calls.append(("login", user, password))
        def send_message(self, message): calls.append(("send", message["To"], message["Subject"]))

    monkeypatch.setattr(send.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SONAR_SMTP_PASSWORD", "app-pass")
    send.send_report(make_report(matches=[make_match()]), settings())
    assert calls == [("connect", "smtp.example", 587), "starttls", ("login", "a@example.com", "app-pass"),
                     ("send", "b@example.com", "Sonar: 1 match (run 3)")]


def test_send_needs_password_and_addresses(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("SONAR_SMTP_PASSWORD", raising=False)
    monkeypatch.setattr(secrets, "ENV_FILE", tmp_path / "missing.env")  # never read the real .env in tests
    with pytest.raises(RuntimeError, match="SONAR_SMTP_PASSWORD"):
        send.send_report(make_report(), settings())
    with pytest.raises(RuntimeError, match="missing"):
        send.send_report(make_report(), Settings(email=True))


def test_subject_flags_a_bad_run() -> None:
    assert send.build_message(make_report(status="failed"), settings())["Subject"].startswith("[RUN FAILED]")
    assert send.build_message(make_report(status="partial"), settings())["Subject"].startswith("[SOME SITES FAILED]")
    assert send.build_message(make_report(), settings())["Subject"] == "Sonar: 0 matches (run 3)"


def test_password_is_read_from_env_file(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SONAR_SMTP_PASSWORD=from-file\n", encoding="utf-8")
    monkeypatch.delenv("SONAR_SMTP_PASSWORD", raising=False)
    monkeypatch.setattr(secrets, "ENV_FILE", env_file)
    assert secrets.smtp_password() == "from-file"
    monkeypatch.delenv("SONAR_SMTP_PASSWORD")  # load_dotenv put it in os.environ; clean up


def test_email_shows_dates_and_how_to_apply() -> None:
    body = render_email_text(make_report(matches=[make_match(
        posted_date="2026-03-11", closing_date="2026-12-31 (unsure, please verify)", how_to_apply="Submit through the portal.")]))
    assert "Date posted: 2026-03-11" in body
    assert "Date due: 2026-12-31 (unsure, please verify)" in body
    assert "How to apply: Submit through the portal." in body
    assert body.index("Date due") < body.index("Keyword matches")


def test_email_marks_missing_dates() -> None:
    body = render_email_text(make_report(matches=[make_match(summary=None)]))
    assert "Date posted: not found, please verify" in body and "Date due: not available" in body and "How to apply" not in body
