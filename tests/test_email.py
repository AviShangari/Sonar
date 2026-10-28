from html import unescape

import pytest

from sonar.config import Settings
from sonar.models import KeywordHit, Report, ReportMatch
from sonar import secrets
from sonar.report import send
from sonar.report.render import render_email_html, render_email_text


def make_report(status: str = "ok", failed: str | None = None, matches: list[ReportMatch] | None = None,
                portals: list[str] | None = None) -> Report:
    return Report(run_id=3, status=status, started_at="October 11, 2026, 09:00 EDT", failed_sites=failed,
                  failed_portals=portals or [], new_postings=5, scored=5, matcher="keywords", matches=matches or [])


def make_match(summary: str | None = "A short summary.", **extra) -> ReportMatch:
    return ReportMatch(
        **{"title": "SAP support", "url": "https://x.example/a", "site": "demo", "job_id": "a", "summary": summary, **extra},
        hits=[KeywordHit(keyword="SAP", count=3, snippet="we need SAP help"),
              KeywordHit(keyword="Project Management", count=2, snippet="project management lead"),
              KeywordHit(keyword="ERP", count=1, snippet="an ERP rollout")],
    )


def settings(**kw) -> Settings:
    return Settings(email=True, smtp_host="smtp.example", email_from="a@example.com", email_to="b@example.com", **kw)


def full_match(**extra) -> ReportMatch:
    fields = dict(
        posted_date="2026-03-11", supply_arrangement="TBIPS", solicitation_number="WS123-45", department="Health Canada",
        closing_date="2026-12-31", summary="Provide SAP support.",
    )
    return make_match(**{**fields, **extra})


def test_html_email_is_one_compact_table_per_portal_with_one_row_per_match() -> None:
    html = render_email_html(make_report(matches=[full_match(), full_match(title="Second", job_id="b")]))
    headings = ["Date posted", "Title", "Solicitation number", "Supply arrangement", "Issuing department", "Due date"]
    assert [html.index(f">{h}<") for h in headings] == sorted(html.index(f">{h}<") for h in headings)
    for value in ("2026-03-11", "TBIPS", "SAP support", "WS123-45", "Provide SAP support.", "Health Canada", "2026-12-31"):
        assert value in html
    assert html.count('class="row') == 2 and html.count("<th") == 6  # one header, two rows


def test_matches_are_grouped_by_portal_biggest_first_and_two_logins_share_one_portal() -> None:
    matches = [make_match(title="A1", site="canadabuys"), make_match(title="B1", site="ariba"),
               make_match(title="B2", site="ariba2"), make_match(title="A2", site="canadabuys"),
               make_match(title="C1", site="ariba")]
    report = make_report(matches=matches)
    html, text = render_email_html(report), render_email_text(report)
    for body in (html, text):
        assert body.lower().index("sap ariba") < body.lower().index("canadabuys")
    assert html.index(">B1<") < html.index(">C1<") < html.index(">A1<")  # Ariba has 3, CanadaBuys 2
    assert "3 matches" in html and "2 matches" in html
    assert [line[:2] for line in text.splitlines() if line[:2] in ("1.", "2.", "3.", "4.", "5.")] == ["1.", "2.", "3.", "4.", "5."]


def test_title_is_a_link_and_keywords_and_snippets_are_gone() -> None:
    report = make_report(matches=[full_match()])
    html, text = render_email_html(report), render_email_text(report)
    assert '<a href="https://x.example/a"' in html and ">SAP support</a>" in html
    for body in (html, text):
        assert "we need SAP help" not in body and "mention" not in body.lower() and "Keyword" not in body
        assert "Project Management" not in body and "more)" not in body


def test_html_email_escapes_posting_text_and_refuses_non_http_links() -> None:
    evil = full_match(title="<script>alert(1)</script>", url="javascript:alert(1)", summary="<b>x</b>", department="A & B")
    html = render_email_html(make_report(matches=[evil]))
    assert "<script>" not in html and "&lt;script&gt;" in html and "<b>x</b>" not in html
    assert 'href="#"' in html and "javascript:" not in html and "A &amp; B" in html


def test_plain_text_copy_has_the_same_fields() -> None:
    text = render_email_text(make_report(matches=[full_match()]))
    for line in ("SAP support", "Link: https://x.example/a", "Date posted: 2026-03-11", "Supply arrangement: TBIPS",
                 "Solicitation number: WS123-45", "Issuing department: Health Canada", "Due date: 2026-12-31", "Provide SAP support."):
        assert line in text


def test_unread_posting_shows_placeholders_not_blanks() -> None:
    report = make_report(matches=[make_match(summary=None)])
    html, text = render_email_html(report), render_email_text(report)
    assert "Summary not available" in html and "Summary not available" in text
    assert "Supply arrangement: not available" in text and "Due date: not available" in text
    assert "not found, please verify" in html  # no posted date


def test_the_sent_message_has_a_text_part_and_an_html_table(monkeypatch) -> None:
    message = send.build_message(make_report(matches=[full_match()]), settings())
    assert message.get_body(("plain",)).get_content().startswith("SONAR: 1 match")
    assert "<table" in message.get_body(("html",)).get_content()


def test_email_is_produced_with_zero_matches() -> None:
    report = make_report()
    for body in (render_email_text(report), render_email_html(report)):
        assert "No new postings matched" in body


def test_a_partial_run_gets_one_calm_line_naming_the_portal_and_no_technical_error() -> None:
    report = make_report(status="partial", failed="canadabuys: OpenFailures: 5 postings could not be opened", portals=["CanadaBuys"])
    for body in (render_email_text(report), unescape(render_email_html(report))):
        assert "We couldn't check CanadaBuys today" in body and "try again later" in body
        assert "OpenFailures" not in body and "RUN FAILED" not in body and "incomplete" in body
    both = make_report(status="partial", portals=["CanadaBuys", "Ontario Tenders", "SAP Ariba"])
    assert "CanadaBuys, Ontario Tenders and SAP Ariba" in render_email_html(both)
    stopped = make_report(status="partial", failed="COST LIMIT: limit reached")  # no failed site: a cost limit cut it short
    assert "stopped early" in render_email_html(stopped) and "COST LIMIT" not in render_email_html(stopped)


def test_a_run_where_everything_failed_gets_the_banner_and_a_partial_run_does_not() -> None:
    failed = make_report(status="failed", failed="canadabuys: boom", portals=["CanadaBuys"])
    for body in (render_email_text(failed), unescape(render_email_html(failed))):
        assert "Today's run failed" in body and "boom" not in body
    for status in ("ok", "partial"):
        assert "run failed" not in render_email_html(make_report(status=status))


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


def test_subject_is_plain_unless_the_run_failed_with_nothing_to_show() -> None:
    subject = lambda report: send.build_message(report, settings())["Subject"]
    assert subject(make_report(status="failed")) == "Sonar: today's run failed (run 3)"
    assert subject(make_report(status="partial")) == "Sonar: 0 matches (run 3)"  # no alarm prefix for the other team
    assert subject(make_report(status="failed", matches=[make_match()])) == "Sonar: 1 match (run 3)"
    assert subject(make_report()) == "Sonar: 0 matches (run 3)"


def test_password_is_read_from_env_file(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SONAR_SMTP_PASSWORD=from-file\n", encoding="utf-8")
    monkeypatch.delenv("SONAR_SMTP_PASSWORD", raising=False)
    monkeypatch.setattr(secrets, "ENV_FILE", env_file)
    assert secrets.smtp_password() == "from-file"
    monkeypatch.delenv("SONAR_SMTP_PASSWORD")  # load_dotenv put it in os.environ; clean up


def test_email_shows_the_due_date_flag_and_leaves_out_how_to_apply() -> None:
    report = make_report(matches=[full_match(closing_date="2026-12-31 (unsure, please verify)", how_to_apply="Submit through the portal.")])
    for body in (render_email_text(report), render_email_html(report)):
        assert "2026-12-31 (unsure, please verify)" in body and "Submit through the portal." not in body


def test_a_very_long_email_stays_under_gmails_clipping_limit_by_shortening_summaries() -> None:
    long_summary = "The department seeks a vendor for ERP support and project management. " * 12
    report = make_report(matches=[full_match(title=f"Posting {i}", job_id=str(i), summary=long_summary) for i in range(60)])
    html = render_email_html(report)
    assert len(html.encode()) < 102_000 and long_summary not in html
    assert long_summary in render_email_html(make_report(matches=[full_match(summary=long_summary)]))  # short emails are untouched
