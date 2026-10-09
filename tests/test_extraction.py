from datetime import date, timedelta

import pytest

from sonar.db import queries
from sonar.db.tables import RunRow, make_engine, utcnow
from sonar.extraction.boilerplate import find_boilerplate, strip_boilerplate
from sonar.extraction.posting import extract_posted_date
from sonar.models import Listing
from sqlalchemy.orm import Session


def posting(i: int) -> str:
    return f"Site banner\nTender {i}\n  Closing date  \nSAP rollout number {i}\nReport a problem"


def test_lines_in_nearly_all_postings_are_boilerplate() -> None:
    texts = [posting(i) for i in range(10)]
    assert find_boilerplate(texts) == {"Site banner", "Closing date", "Report a problem"}


def test_a_line_in_too_few_postings_is_content() -> None:
    texts = [posting(i) for i in range(10)]
    texts[0] += "\nPartner with another business"  # only 1 of 10
    assert "Partner with another business" not in find_boilerplate(texts)


def test_nothing_is_stripped_with_too_few_postings() -> None:
    assert find_boilerplate([posting(i) for i in range(4)]) == set()


def test_strip_keeps_the_posting_content_and_drops_blank_lines() -> None:
    boilerplate = find_boilerplate([posting(i) for i in range(10)])
    assert strip_boilerplate(posting(3), boilerplate) == "Tender 3\nSAP rollout number 3"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Solicitation number X\n\nPublication date\n2026/10/09\n\nClosing date\n2026/11/08", date(2026, 10, 9)),
        ("Published: 2026-10-07", date(2026, 10, 7)),
        ("Posted date: 2026/01/02", date(2026, 1, 2)),
        ("Closing date\n2026/11/08", None),  # only a closing date
        ("Publication date\n2026/13/45", None),  # impossible date
        ("no dates here", None),
    ],
)
def test_extract_posted_date(text: str, expected: date | None) -> None:
    assert extract_posted_date(text) == expected


def test_stale_running_runs_are_marked_failed_but_recent_ones_are_not() -> None:
    engine = make_engine("sqlite://")
    old, recent = queries.start_run(engine), queries.start_run(engine)
    with Session(engine) as session:
        session.get(RunRow, old).started_at = utcnow() - timedelta(hours=5)
        session.commit()
    assert queries.fail_stale_runs(engine) == 1
    with Session(engine) as session:
        assert session.get(RunRow, old).status == "failed"
        assert session.get(RunRow, old).finished_at is not None
        assert session.get(RunRow, recent).status == "running"


def test_site_posting_texts_returns_only_that_sites_texts() -> None:
    engine = make_engine("sqlite://")
    run_id = queries.start_run(engine)
    for site, job in [("a", "J1"), ("a", "J2"), ("b", "J3")]:
        queries.add_listing(
            engine,
            Listing(site=site, job_id=job, url="https://x/" + job, posting_text=f"text {job}", text_hash=job),
            run_id,
        )
    assert sorted(queries.site_posting_texts(engine, "a")) == ["text J1", "text J2"]
