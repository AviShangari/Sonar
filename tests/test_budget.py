"""Cost caps: hitting a limit ends the run as partial, says why, and never moves a watermark."""

import asyncio

import pytest

from sonar import budget, llm
from sonar.budget import BudgetExceeded, RunBudget
from sonar.config import Settings
from sonar.db import queries
from sonar.matching.keywords import KeywordMatcher
from sonar.matching.run import match_new_listings
from sonar.models import Listing
from sonar.summarize.summaries import PostingInfo, summarize_matches
from test_main_delivery import wired  # noqa: F401  (fixture)
from test_watermark import SITE, FakeNavigator, collect, engine, wire  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def reset_budget():
    yield
    budget.configure(RunBudget())  # never leak a limit into other tests


def test_agent_calls_stop_at_the_limit_and_it_is_noted() -> None:
    b = RunBudget(max_agent_calls=2)
    budget.configure(b)

    async def calls() -> None:
        await b.charge_agent_call()
        await b.charge_agent_call()
        await b.charge_agent_call()

    with pytest.raises(BudgetExceeded):
        asyncio.run(calls())
    assert b.agent_calls == 2 and b.exhausted and "agent_calls" in b.hits
    with pytest.raises(BudgetExceeded):  # once exhausted, every later call is refused at once
        asyncio.run(b.charge_agent_call())


def test_no_limits_means_no_refusals() -> None:
    b = RunBudget()
    for _ in range(500):
        asyncio.run(b.charge_agent_call())
    assert not b.exhausted and not b.hits


def test_dollar_limit_uses_the_keys_spend_since_the_run_began(monkeypatch) -> None:
    usage = iter([10.00, 10.00, 10.40, 10.60])  # begin, call 1, call 2, call 3

    async def fake_usage() -> float:
        return next(usage)

    monkeypatch.setattr(llm, "openrouter_usage_usd", fake_usage)
    b = RunBudget(max_run_usd=0.50)

    async def go() -> None:
        await b.begin()
        await b.charge_agent_call()  # spent 0.00
        await b.charge_agent_call()  # spent 0.40
        await b.charge_agent_call()  # spent 0.60: over

    with pytest.raises(BudgetExceeded):
        asyncio.run(go())
    assert b.agent_calls == 2 and "run_usd" in b.hits


def test_an_unknown_spend_never_blocks_the_run(monkeypatch) -> None:
    async def unknown() -> None:
        return None

    monkeypatch.setattr(llm, "openrouter_usage_usd", unknown)
    b = RunBudget(max_run_usd=0.01)
    asyncio.run(b.begin())
    asyncio.run(b.charge_agent_call())  # lookup failed: allowed, because the count caps still apply
    assert b.agent_calls == 1


def test_the_usage_lookup_is_off_for_subscription_logins() -> None:
    assert asyncio.run(llm.openrouter_usage_usd()) is None  # no network call, no key needed


def test_a_site_cap_stops_that_site_leaves_the_watermark_and_is_noted(engine, wire) -> None:  # noqa: F811
    b = RunBudget(max_new_per_site=2)
    budget.configure(b)
    wire["navigator"] = FakeNavigator([("a", "2026-10-09"), ("b", "2026-10-08"), ("c", "2026-10-07")])
    outcome = collect(engine)
    assert outcome.stored == 2 and not queries.listing_exists(engine, "demo", "c")
    assert queries.get_watermark(engine, "demo") is None  # the next run re-reads down to the old stop point
    assert "posts:demo" in b.hits and not b.exhausted  # other sites may still run


def test_a_site_cap_that_is_not_reached_is_clean(engine, wire) -> None:  # noqa: F811
    b = RunBudget(max_new_per_site=5)
    budget.configure(b)
    wire["navigator"] = FakeNavigator([("a", "2026-10-09"), ("b", "2026-10-08")])
    collect(engine)
    assert not b.hits and queries.get_watermark(engine, "demo") is not None


def test_a_run_wide_limit_during_navigation_ends_the_site_without_failing_it(engine, wire) -> None:  # noqa: F811
    class OutOfBudget(FakeNavigator):
        async def next_listing(self):
            raise BudgetExceeded("limit reached")

    wire["navigator"] = OutOfBudget([])
    outcome = collect(engine)  # does not raise
    assert outcome.error is None and queries.get_watermark(engine, "demo") is None


def test_the_summary_cap_leaves_the_rest_without_summaries() -> None:
    engine, run_id = make_engine_with_matches(5)
    b = RunBudget(max_summaries=2)
    budget.configure(b)

    async def summarizer(title: str, text: str) -> PostingInfo:
        return PostingInfo(summary="ok")

    written = asyncio.run(summarize_matches(engine, run_id, "keywords", summarizer=summarizer))
    assert written == 2 and "summaries" in b.hits and "3 matches" in b.hits["summaries"]


def make_engine_with_matches(n: int):
    from sonar.db.tables import make_engine

    engine = make_engine("sqlite://")
    run_id = queries.start_run(engine)
    for i in range(n):
        queries.add_listing(
            engine,
            Listing(site="demo", job_id=f"J{i}", url=f"https://x/J{i}", title=f"T{i}", posting_text="We need SAP help",
                    text_hash=str(i), fields={"listed_date": "2026-10-09"}),
            run_id,
        )
    match_new_listings(engine, KeywordMatcher(["SAP"]), "keywords", "demo", run_id)
    return engine, run_id


def test_settings_defaults_are_finite_for_counts_and_off_for_dollars() -> None:
    s = Settings()
    assert s.max_agent_calls and s.max_new_per_site and s.max_summaries
    assert s.max_run_usd is None and s.max_call_usd is None
    b = RunBudget.from_settings(s)
    assert b.max_agent_calls == s.max_agent_calls


def test_a_run_that_hits_a_limit_is_partial_names_the_limit_and_skips_later_sites(wired, monkeypatch) -> None:  # noqa: F811
    engine, sent, _ = wired
    main = main_module()
    monkeypatch.setattr(main, "load_sites", lambda: [SITE, SITE.model_copy(update={"name": "second"})])
    started: list[str] = []

    async def hits_the_cap(site, settings, engine, matcher, run_id, days, max_new):
        started.append(site.name)
        budget.current.note_hit("agent_calls", "limit of 80 agent calls reached", stop_all=True)
        return main.SiteOutcome(site.name, decisions=[])

    monkeypatch.setattr(main, "run_site", hits_the_cap)
    assert asyncio.run(main.run(None, None, None)) == 1
    assert started == ["demo"]  # the second site was not started
    assert sent[0].status == "partial"
    assert "COST LIMIT: limit of 80 agent calls reached" in sent[0].failed_sites and "second was not started" in sent[0].failed_sites


def main_module():
    import importlib

    return importlib.import_module("sonar.main")


def test_the_final_cost_waits_for_openrouters_delayed_counter(monkeypatch) -> None:
    readings = iter([10.0, 10.02, 10.05, 10.09, 10.09, 10.09])  # begin, then the counter catching up, then steady

    async def fake_usage() -> float:
        return next(readings)

    monkeypatch.setattr(llm, "openrouter_usage_usd", fake_usage)
    b = RunBudget()
    asyncio.run(b.begin())
    spent = asyncio.run(b.final_spent_usd(wait_s=0))
    assert spent == pytest.approx(0.09)
