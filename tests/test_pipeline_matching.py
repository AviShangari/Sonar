import pytest

from sonar.config import Settings, load_settings
from sonar.db import queries
from sonar.db.tables import make_engine
from sonar.main import run_status
from sonar.matching.factory import build_matcher
from sonar.matching.keywords import KeywordMatcher
from sonar.extraction.boilerplate import find_boilerplate
from sonar.matching.run import match_new_listings, warn_keywords_in_boilerplate
from sonar.models import Listing
from sonar.pipeline import SiteOutcome

BANNER = "Oracle sponsored banner"  # on every page, so it must not cause matches


@pytest.fixture()
def engine():
    return make_engine("sqlite://")


def store(engine, run_id: int, job_id: str, body: str, site: str = "demo") -> None:
    queries.add_listing(
        engine,
        Listing(site=site, job_id=job_id, url=f"https://x/{job_id}", posting_text=f"{BANNER}\n{body}", text_hash=job_id),
        run_id,
    )


def fill(engine, run_id: int) -> None:
    for i in range(15):
        store(engine, run_id, f"J{i}", f"Plain tender number {i}")
    store(engine, run_id, "SAP1", "We need SAP and change management help")


def test_matching_uses_cleaned_text_so_site_banners_do_not_match(engine) -> None:
    run_id = queries.start_run(engine)
    fill(engine, run_id)
    decisions = match_new_listings(engine, KeywordMatcher(["SAP", "Oracle"]), "keywords", "demo", run_id)
    assert len(decisions) == 16
    matched = [d for d in decisions if d.matched]
    assert len(matched) == 1
    assert [h["keyword"] for h in matched[0].details["hits"]] == ["SAP"]  # Oracle only appears in the banner


def test_stored_text_stays_raw(engine) -> None:
    run_id = queries.start_run(engine)
    fill(engine, run_id)
    match_new_listings(engine, KeywordMatcher(["SAP"]), "keywords", "demo", run_id)
    (row,) = [r for r in queries.undecided_listings(engine, "demo", "other-matcher") if r.job_id == "SAP1"]
    assert row.posting_text.startswith(BANNER)


def test_matching_twice_decides_each_listing_once(engine) -> None:
    run_id = queries.start_run(engine)
    fill(engine, run_id)
    matcher = KeywordMatcher(["SAP"])
    assert len(match_new_listings(engine, matcher, "keywords", "demo", run_id)) == 16
    assert match_new_listings(engine, matcher, "keywords", "demo", run_id) == []
    assert len(queries.matched_listings(engine, run_id)) == 1


def test_a_new_matcher_is_not_blocked_by_another_matchers_decisions(engine) -> None:
    run_id = queries.start_run(engine)
    fill(engine, run_id)
    match_new_listings(engine, KeywordMatcher(["SAP"]), "keywords", "demo", run_id)
    assert len(queries.undecided_listings(engine, "demo", "jev")) == 16
    assert queries.undecided_listings(engine, "demo", "keywords") == []


def test_only_the_named_site_is_matched(engine) -> None:
    run_id = queries.start_run(engine)
    fill(engine, run_id)
    store(engine, run_id, "S9", "SAP here", site="other")
    assert len(match_new_listings(engine, KeywordMatcher(["SAP"]), "keywords", "demo", run_id)) == 16
    assert len(queries.undecided_listings(engine, "other", "keywords")) == 1


def test_settings_load_and_pick_the_built_matcher() -> None:
    settings = load_settings()
    assert (settings.navigator, settings.matcher) == ("dom", "keywords")
    assert isinstance(build_matcher(settings), KeywordMatcher)


def test_unbuilt_matcher_fails_clearly() -> None:
    with pytest.raises(NotImplementedError, match="jev"):
        build_matcher(Settings(matcher="jev"))


def test_run_status_reflects_how_many_sites_failed() -> None:
    ok, bad = SiteOutcome("a"), SiteOutcome("b", error="boom")
    assert run_status([ok]) == "ok"
    assert run_status([ok, bad]) == "partial"
    assert run_status([bad]) == "failed"


def test_a_keyword_hidden_in_boilerplate_is_reported(engine) -> None:
    run_id = queries.start_run(engine)
    fill(engine, run_id)
    texts = queries.site_posting_texts(engine, "demo")
    warnings = warn_keywords_in_boilerplate("demo", texts, find_boilerplate(texts), KeywordMatcher(["SAP", "Oracle"]))
    assert len(warnings) == 1  # only the banner line has a keyword
    assert "Oracle" in warnings[0] and "16/16" in warnings[0]


def test_no_warning_when_boilerplate_has_no_keywords(engine) -> None:
    run_id = queries.start_run(engine)
    fill(engine, run_id)
    texts = queries.site_posting_texts(engine, "demo")
    assert warn_keywords_in_boilerplate("demo", texts, find_boilerplate(texts), KeywordMatcher(["SAP"])) == []
