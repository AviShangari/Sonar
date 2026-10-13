from sonar.db import queries
from sonar.db.tables import make_engine
from sonar.matching.keywords import KeywordMatcher
from sonar.matching.run import match_new_listings
from sonar.models import KeywordHit, Listing, Report, ReportMatch
from sonar.report.build import build_report
from sonar.report.render import highlight, render_html, render_text, write_report


def store(engine, run_id: int, job_id: str, body: str, title: str = "A tender", listed: str = "2026-10-09") -> None:
    queries.add_listing(
        engine,
        Listing(site="demo", job_id=job_id, url=f"https://x.example/{job_id}", title=title, posting_text=body,
                text_hash=job_id, fields={"listed_date": listed, "posted_date": None}),
        run_id,
    )


def make_run(engine):
    run_id = queries.start_run(engine)
    store(engine, run_id, "A1", "Plain tender")
    store(engine, run_id, "B1", "We need SAP help", title="One keyword", listed="2026-10-01")
    store(engine, run_id, "C1", "SAP and change management, SAP again", title="Two keywords")
    store(engine, run_id, "D1", "SAP help too", title="One keyword, newer", listed="2026-10-08")
    match_new_listings(engine, KeywordMatcher(["SAP", "change management"]), "keywords", "demo", run_id)
    return run_id


def test_report_counts_and_ranking() -> None:
    engine = make_engine("sqlite://")
    report = build_report(engine, make_run(engine), "keywords")
    assert (report.new_postings, report.scored, len(report.matches)) == (4, 4, 3)
    # Two different keywords first; between equals, the newer listing first.
    assert [m.title for m in report.matches] == ["Two keywords", "One keyword, newer", "One keyword"]
    assert report.failed_sites is None


def test_report_is_produced_with_zero_matches() -> None:
    engine = make_engine("sqlite://")
    run_id = queries.start_run(engine)
    report = build_report(engine, run_id, "keywords")
    assert report.matches == []
    assert "No new postings matched" in render_html(report)
    assert "No new postings matched" in render_text(report)


def test_failed_sites_are_shown_at_the_top() -> None:
    engine = make_engine("sqlite://")
    run_id = queries.start_run(engine)
    queries.finish_run(engine, run_id, "partial", "siteB: TimeoutError: boom")
    report = build_report(engine, run_id, "keywords")
    html, text = render_html(report), render_text(report)
    assert "This run was incomplete" in html and "siteB: TimeoutError: boom" in html
    assert html.index("siteB") < html.index("Matches")
    assert "THIS RUN WAS INCOMPLETE" in text and text.index("siteB") < text.index("0 matches")


def test_untrusted_text_is_escaped_and_bad_links_are_neutralized() -> None:
    report = Report(
        run_id=1, status="ok", started_at="now", new_postings=1, scored=1, matcher="keywords",
        matches=[ReportMatch(
            title="<script>alert(1)</script>", url="javascript:alert(1)", site="s", job_id="j",
            hits=[KeywordHit(keyword="SAP", count=1, snippet="<img src=x onerror=alert(1)> SAP here")],
        )],
    )
    html = render_html(report)
    assert "<script>alert(1)</script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html
    assert 'href="javascript:' not in html and 'href="#"' in html
    assert "<mark>SAP</mark>" in html


def test_highlight_only_marks_whole_words() -> None:
    assert str(highlight("SAP and saplings", "SAP")) == "<mark>SAP</mark> and saplings"


def test_files_are_written(tmp_path) -> None:
    engine = make_engine("sqlite://")
    run_id = make_run(engine)
    paths = write_report(build_report(engine, run_id, "keywords"), tmp_path)
    assert sorted(p.name for p in paths) == [f"report-run-{run_id}.html", f"report-run-{run_id}.txt"]
    text = (tmp_path / f"report-run-{run_id}.txt").read_text(encoding="utf-8")
    assert "1. Two keywords" in text and "SAP (x2)" in text and "https://x.example/C1" in text
    assert max(len(line) for line in text.splitlines()) <= 90


def test_run_time_is_shown_in_the_given_time_zone_and_utc_by_default() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from sqlalchemy.orm import Session

    from sonar.db.tables import RunRow

    engine = make_engine("sqlite://")
    run_id = queries.start_run(engine)
    toronto = ZoneInfo("America/Toronto")
    for stored_utc, shown in ((datetime(2026, 10, 13, 13, 0), "October 13, 2026, 09:00 EDT"),  # daylight time
                              (datetime(2026, 12, 13, 13, 0), "December 13, 2026, 08:00 EST")):  # standard time
        with Session(engine) as session:
            session.get(RunRow, run_id).started_at = stored_utc  # the database stores naive UTC
            session.commit()
        assert build_report(engine, run_id, "keywords", toronto).started_at == shown
    assert build_report(engine, run_id, "keywords").started_at == "December 13, 2026, 13:00 UTC"


def test_a_single_digit_day_has_no_leading_zero() -> None:
    from datetime import datetime

    from sonar.report.build import _long_date_time

    assert _long_date_time(datetime(2026, 10, 5, 9, 5)) == "October 5, 2026, 09:05 "  # %Z is empty for a naive time
