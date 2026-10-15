"""Jev matcher tests. No test makes a real call: httpx is given a fake server (MockTransport)."""

import json

import httpx
import pytest
import yaml

from sonar.config import Settings
from sonar.db import queries
from sonar.db.tables import make_engine
from sonar.extraction.boilerplate import find_boilerplate
from sonar.matching.factory import build_matcher
from sonar.matching.jev import Company, JevMatcher, load_company
from sonar.matching.run import match_new_listings
from sonar.models import Listing
from sonar.report.build import build_report
from sonar.report.render import render_email_html, render_email_text, render_html, render_text

COMPANY = Company(
    what_we_do="We sell SAP consulting.", question="Would we bid?", yes_means="SAP work.", no_means="Anything else.",
)
KEY = "sk-or-secret-key"


def reply(probability: float, cost: float = 0.00002) -> dict:
    return {"id": "x", "answers": {"relevant": {"type": "noul", "noul": probability}}, "usage": {"cost": cost}}


class FakeJev:
    """Stands in for the service: answers from a list (a number, or an HTTP status / exception to simulate)."""

    def __init__(self, *outcomes) -> None:
        self.outcomes, self.requests = list(outcomes), []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, int):
            return httpx.Response(outcome, json={"error": {"message": "nope"}})
        if isinstance(outcome, dict):
            return httpx.Response(200, json=outcome)
        return httpx.Response(200, json=reply(outcome))

    def matcher(self, **kwargs) -> JevMatcher:
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return JevMatcher(COMPANY, KEY, client=client, sleep=lambda _s: None, **kwargs)


def test_the_request_has_one_yes_no_question_with_the_company_text_outside_the_posting() -> None:
    fake = FakeJev(0.9)
    fake.matcher().match("We need SAP help")
    (request,) = fake.requests
    body = json.loads(request.content)
    assert str(request.url) == "https://openrouter.ai/api/alpha/decisions"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert body["model"] == "typesafe/jev-1.13"
    assert body["state"] == {"posting_text": "We need SAP help"}  # the company text is not in the data
    (name, question), = body["questions"].items()
    assert question["type"] == "noul"
    assert "We sell SAP consulting." in question["instructions"] and "Would we bid?" in question["instructions"]
    assert question["criteria"] == {"true": "SAP work.", "false": "Anything else."}


@pytest.mark.parametrize(
    "probability, matched, review",
    [(1.0, True, False), (0.6, True, False), (0.59, True, True), (0.3, True, True), (0.29, False, False), (0.0, False, False)],
)
def test_thresholds(probability: float, matched: bool, review: bool) -> None:
    result = FakeJev(probability).matcher().match("text")
    assert (result.matched, result.needs_review, result.score) == (matched, review, probability)
    assert not result.unscored


def test_thresholds_come_from_the_settings() -> None:
    result = FakeJev(0.45).matcher(match_threshold=0.4, review_threshold=0.2).match("text")
    assert result.matched and not result.needs_review


def test_wrong_threshold_order_is_refused() -> None:
    with pytest.raises(ValueError):
        Settings(jev_review_threshold=0.7, jev_match_threshold=0.6)
    with pytest.raises(ValueError):
        JevMatcher(COMPANY, KEY, match_threshold=0.3, review_threshold=0.6)


def test_long_postings_are_cut() -> None:
    fake = FakeJev(0.9)
    fake.matcher(max_chars=10).match("x" * 100)
    assert json.loads(fake.requests[0].content)["state"]["posting_text"] == "x" * 10


def test_the_cost_is_recorded() -> None:
    matcher = FakeJev(0.9).matcher()
    assert matcher.match("t").cost_usd == 0.00002
    assert matcher.total_cost_usd == pytest.approx(0.00002)


def test_a_server_error_is_retried_then_succeeds() -> None:
    fake = FakeJev(503, 0.8)
    result = fake.matcher().match("t")
    assert result.matched and len(fake.requests) == 2


@pytest.mark.parametrize("outcome", [500, 429, httpx.ReadTimeout("slow"), httpx.ConnectError("down")])
def test_jev_down_means_unscored_never_a_non_match(outcome) -> None:
    fake = FakeJev(outcome)
    result = fake.matcher().match("t")
    assert result.unscored and result.matched is None
    assert len(fake.requests) == 3  # one try plus two retries
    assert result.error


def test_a_rejected_request_is_not_retried() -> None:
    fake = FakeJev(401)
    result = fake.matcher().match("t")
    assert result.unscored and len(fake.requests) == 1
    assert "401" in result.error and KEY not in result.error


@pytest.mark.parametrize(
    "bad", [{"answers": {}}, {"answers": {"relevant": {"type": "noul", "noul": 1.5}}},
            {"answers": {"relevant": {"type": "choice", "choice": "a"}}}, {"answers": {"relevant": {"type": "noul", "noul": "yes"}}}, {}],
)
def test_a_reply_in_the_wrong_shape_is_unscored(bad) -> None:
    result = FakeJev(bad).matcher().match("t")
    assert result.unscored and "format" in result.error


def test_after_five_failures_in_a_row_jev_is_not_called_again() -> None:
    fake = FakeJev(401)
    matcher = fake.matcher()
    results = [matcher.match("t") for _ in range(8)]
    assert all(r.unscored for r in results)
    assert len(fake.requests) == 5  # the last three were skipped without a call


def test_a_success_resets_the_failure_count() -> None:
    fake = FakeJev(401, 401, 0.9, 401, 401)
    matcher = fake.matcher()
    results = [matcher.match("t") for _ in range(5)]
    assert [r.unscored for r in results] == [True, True, False, True, True]


def test_company_text_is_required(tmp_path) -> None:
    path = tmp_path / "company.yaml"
    path.write_text("what_we_do: |\n\nquestion: q\nyes_means: y\nno_means: n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="what_we_do"):
        load_company(path)
    path.write_text("what_we_do: We do things.\nquestion: q\nyes_means: y\nno_means: n\n", encoding="utf-8")
    assert load_company(path).what_we_do == "We do things."


def test_the_shipped_company_file_has_every_field() -> None:
    # `what_we_do` is left for the developer to write; the other fields must be there either way
    values = yaml.safe_load(Settings().company_path.read_text(encoding="utf-8"))
    assert {"what_we_do", "question", "yes_means", "no_means"} <= set(values)
    assert all(values[key] for key in ("question", "yes_means", "no_means"))


def test_the_factory_builds_jev(tmp_path, monkeypatch) -> None:
    company = tmp_path / "company.yaml"
    company.write_text("what_we_do: We do things.\nquestion: q\nyes_means: y\nno_means: n\n", encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    settings = Settings(matcher="jev", company_file=str(company))
    assert isinstance(build_matcher(settings), JevMatcher)


# --- pipeline, retry and report ---


@pytest.fixture()
def engine():
    return make_engine("sqlite://")


BANNER = "Shared banner line"


def store(engine, run_id: int, job_id: str) -> None:
    queries.add_listing(
        engine, Listing(site="demo", job_id=job_id, url=f"https://x/{job_id}", title=f"Tender {job_id}",
                        posting_text=f"{BANNER}\nTender {job_id} body", text_hash=job_id), run_id,
    )


def test_unscored_listings_are_matched_again_on_the_next_run(engine) -> None:
    first = queries.start_run(engine)
    for i in range(3):
        store(engine, first, f"J{i}")
    down = match_new_listings(engine, FakeJev(500).matcher(), "jev", "demo", first)
    assert [d.matched for d in down] == [None, None, None]
    assert len(queries.undecided_listings(engine, "demo", "jev")) == 3  # not decided: still waiting

    second = queries.start_run(engine)
    back = match_new_listings(engine, FakeJev(0.9, 0.5, 0.1).matcher(), "jev", "demo", second)
    assert [d.matched for d in back] == [True, True, False]
    assert queries.undecided_listings(engine, "demo", "jev") == []
    assert match_new_listings(engine, FakeJev(0.9).matcher(), "jev", "demo", second) == []  # decided: not asked again
    report = build_report(engine, first, "jev")
    assert (report.scored, report.unscored) == (0, 0)  # the old unscored rows were replaced, not duplicated


def test_jev_does_not_run_the_keyword_boilerplate_check(engine) -> None:
    run_id = queries.start_run(engine)
    for i in range(20):
        store(engine, run_id, f"J{i}")
    assert find_boilerplate(queries.site_posting_texts(engine, "demo")) == {BANNER}  # there IS a stripped line
    fake = FakeJev(0.1)
    match_new_listings(engine, fake.matcher(), "jev", "demo", run_id)
    assert len(fake.requests) == 20  # one call per posting, none for boilerplate lines


def test_score_and_review_flag_reach_the_database_and_report(engine) -> None:
    run_id = queries.start_run(engine)
    for i in range(3):
        store(engine, run_id, f"J{i}")
    match_new_listings(engine, FakeJev(0.45, 0.95, 0.1).matcher(), "jev", "demo", run_id)
    report = build_report(engine, run_id, "jev")
    assert [(m.job_id, m.score, m.needs_review) for m in report.matches] == [("J1", 0.95, False), ("J0", 0.45, True)]
    assert report.scored == 3 and report.unscored == 0
    for text in (render_html(report), render_text(report), render_email_html(report), render_email_text(report)):
        assert "Needs review" in text or "NEEDS REVIEW" in text
    assert "could not be scored" not in render_email_html(report)


def test_the_email_says_when_postings_could_not_be_scored(engine) -> None:
    run_id = queries.start_run(engine)
    for i in range(2):
        store(engine, run_id, f"J{i}")
    match_new_listings(engine, FakeJev(500).matcher(), "jev", "demo", run_id)
    report = build_report(engine, run_id, "jev")
    assert (report.unscored, report.matches) == (2, [])
    for text in (render_email_html(report), render_email_text(report), render_html(report), render_text(report)):
        assert "could not be scored" in text
