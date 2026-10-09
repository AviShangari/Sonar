import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sonar.db import queries
from sonar.db.tables import RunRow, make_engine
from sonar.models import Decision, Listing


@pytest.fixture()
def engine():
    return make_engine("sqlite://")  # in-memory database, fresh per test


def make_listing(job_id: str = "J1", site: str = "siteA") -> Listing:
    return Listing(
        site=site,
        job_id=job_id,
        url=f"https://example.com/{job_id}",
        title="SAP rollout",
        posting_text="We need SAP help.",
        text_hash="abc",
        fields={"client": "Acme", "posted": None},
    )


def test_listing_exists_only_after_it_is_stored(engine) -> None:
    run_id = queries.start_run(engine)
    assert not queries.listing_exists(engine, "siteA", "J1")
    queries.add_listing(engine, make_listing(), run_id)
    assert queries.listing_exists(engine, "siteA", "J1")
    assert not queries.listing_exists(engine, "siteB", "J1")  # same ID, other site


def test_duplicate_site_and_job_id_is_rejected(engine) -> None:
    run_id = queries.start_run(engine)
    queries.add_listing(engine, make_listing(), run_id)
    with pytest.raises(IntegrityError):
        queries.add_listing(engine, make_listing(), run_id)


def test_matched_listings_excludes_non_matches_and_unscored(engine) -> None:
    run_id = queries.start_run(engine)
    ids = [queries.add_listing(engine, make_listing(j), run_id) for j in ("J1", "J2", "J3")]
    for listing_id, matched in zip(ids, (True, False, None)):
        queries.add_decision(
            engine,
            Decision(listing_id=listing_id, run_id=run_id, matcher="keywords", matched=matched),
        )
    ((listing, decision),) = queries.matched_listings(engine, run_id)
    assert listing.job_id == "J1" and decision.matched is True
    assert listing.fields == {"client": "Acme", "posted": None}


def test_finish_run_records_status(engine) -> None:
    run_id = queries.start_run(engine)
    queries.finish_run(engine, run_id, "ok")
    with Session(engine) as session:
        run = session.get(RunRow, run_id)
        assert run.status == "ok" and run.finished_at is not None
