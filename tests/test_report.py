from sonar.db import queries
from sonar.db.tables import make_engine
from sonar.matching.keywords import KeywordMatcher
from sonar.matching.run import match_new_listings
from sonar.models import KeywordHit, Listing, Report, ReportMatch
from sonar.report.build import build_report
from sonar.report.render import highlight, render_email_html, render_email_text, render_html, render_text, write_report


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


def test_document_links_are_detected_in_saved_html() -> None:
    from sonar.extraction.documents import has_document_links

    assert has_document_links('<a href="/sites/default/files/tender/notice-123.pdf">Notice</a>')
    assert has_document_links("<a href='https://x.example/a/Specs.DOCX?download=1'>Specs</a>")
    assert not has_document_links('<a href="/en/privacy">Privacy</a> <a href="/about.html">About</a>')
    assert not has_document_links('<p>see notice.pdf in the text, no link</p>')
    assert not has_document_links(None)


def test_the_review_note_appears_for_flagged_sites_and_for_postings_with_document_links() -> None:
    from sonar.report.build import REVIEW_NOTE

    engine = make_engine("sqlite://")
    run_id = queries.start_run(engine)
    for job_id, site, html in [
        ("P1", "ariba", "<p>no files</p>"),  # flagged site: always
        ("P2", "demo", '<a href="/files/specs.pdf">Specs</a>'),  # a document link
        ("P3", "demo", "<p>nothing attached</p>"),  # neither
    ]:
        queries.add_listing(
            engine,
            Listing(site=site, job_id=job_id, url=f"https://x.example/{job_id}", title=job_id, posting_text="SAP help",
                    raw_html=html, text_hash=job_id, fields={}),
            run_id,
        )
    for site in ("ariba", "demo"):
        match_new_listings(engine, KeywordMatcher(["SAP"]), "keywords", site, run_id)
    report = build_report(engine, run_id, "keywords", note_sites=frozenset({"ariba"}))
    notes = {m.title: m.review_note for m in report.matches}
    assert notes == {"P1": REVIEW_NOTE, "P2": REVIEW_NOTE, "P3": None}
    html, text = render_email_html(report), render_email_text(report)
    assert html.count(REVIEW_NOTE) == 2 and text.count(REVIEW_NOTE) == 2  # one line per flagged match, none for P3
    assert {m.title: m.review_note for m in build_report(engine, run_id, "keywords").matches}["P1"] is None


def test_each_match_names_the_account_that_found_it() -> None:
    engine = make_engine("sqlite://")
    run_id = queries.start_run(engine)
    for site in ("ariba", "ariba2", "demo"):
        queries.add_listing(
            engine,
            Listing(site=site, job_id="E1", url=f"https://x.example/{site}", title=f"Event on {site}",
                    posting_text="SAP help", text_hash=site, fields={}),
            run_id,
        )
        match_new_listings(engine, KeywordMatcher(["SAP"]), "keywords", site, run_id)
    accounts = {"ariba": "Ariba account 1", "ariba2": "Ariba account 2"}
    report = build_report(engine, run_id, "keywords", accounts=accounts)
    assert {m.title: m.account for m in report.matches} == {
        "Event on ariba": "Ariba account 1", "Event on ariba2": "Ariba account 2", "Event on demo": None,
    }  # the same event ID under two accounts stays two listings
    for rendered in (render_email_html(report), render_email_text(report), render_html(report), render_text(report)):
        assert "Ariba account 1" in rendered and "Ariba account 2" in rendered
    assert render_email_text(report).count("Account:") == 2  # none for the site without an account
    plain = build_report(engine, run_id, "keywords")  # no accounts configured: no column, no label
    assert "Account" not in render_email_html(plain) and "Account:" not in render_email_text(plain)


def test_failed_portals_are_read_from_the_run_notes_once_each_and_cost_limits_name_none() -> None:
    from sonar.report.build import failed_portals

    notes = "canadabuys: OpenFailures: 5 postings could not be opened; ariba2: boom; ariba: boom; COST LIMIT: limit of 80 reached"
    assert failed_portals(notes) == ["CanadaBuys", "SAP Ariba"]
    assert failed_portals("COST LIMIT: limit of 80 reached") == [] and failed_portals(None) == []
