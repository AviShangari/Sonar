import asyncio

import pytest

from sonar.agent_runner import isolated_options
from sonar.db import queries
from sonar.db.tables import make_engine
from sonar.matching.keywords import KeywordMatcher
from sonar.matching.run import match_new_listings
from sonar.models import Listing
from sonar.report.build import build_report
from sonar.report.render import render_html, render_text
from sonar.summarize.summaries import MAX_SUMMARY_CHARS, PostingInfo, clean_summary, parse_posting_info, summarize_matches

BANNER = "Site banner"


@pytest.fixture()
def setup():
    """Fifteen plain postings plus two SAP matches, matched on the keywords matcher."""
    engine = make_engine("sqlite://")
    run_id = queries.start_run(engine)
    for i in range(15):
        body = "We need SAP help" if i < 2 else f"Plain tender {i}"
        queries.add_listing(
            engine,
            Listing(site="demo", job_id=f"J{i}", url=f"https://x/J{i}", title=f"Tender {i}",
                    posting_text=f"{BANNER}\n{body}", text_hash=str(i), fields={"listed_date": "2026-10-09"}),
            run_id,
        )
    match_new_listings(engine, KeywordMatcher(["SAP"]), "keywords", "demo", run_id)
    return engine, run_id


def test_only_matches_are_summarized_from_cleaned_text(setup) -> None:
    engine, run_id = setup
    seen: list[tuple[str, str]] = []

    async def summarizer(title: str, text: str) -> PostingInfo:
        seen.append((title, text))
        return PostingInfo(summary="  A short\nsummary.  ")

    assert asyncio.run(summarize_matches(engine, run_id, "keywords", summarizer)) == 2
    assert sorted(t for t, _ in seen) == ["Tender 0", "Tender 1"]  # the 13 non-matches are never sent
    assert all(BANNER not in text and "SAP" in text for _, text in seen)  # banner stripped before sending
    report = build_report(engine, run_id, "keywords")
    assert [m.summary for m in report.matches] == ["A short summary."] * 2


def test_summaries_are_written_once(setup) -> None:
    engine, run_id = setup
    calls: list[str] = []

    async def summarizer(title: str, text: str) -> PostingInfo:
        calls.append(title)
        return PostingInfo(summary="Summary.")

    asyncio.run(summarize_matches(engine, run_id, "keywords", summarizer))
    assert asyncio.run(summarize_matches(engine, run_id, "keywords", summarizer)) == 0
    assert len(calls) == 2


def test_a_failed_summary_does_not_stop_the_others_or_lose_the_listing(setup) -> None:
    engine, run_id = setup

    async def summarizer(title: str, text: str) -> PostingInfo:
        if title == "Tender 0":
            raise RuntimeError("agent down")
        return PostingInfo(summary="Fine.")

    assert asyncio.run(summarize_matches(engine, run_id, "keywords", summarizer)) == 1
    summaries = {m.title: m.summary for m in build_report(engine, run_id, "keywords").matches}
    assert summaries == {"Tender 0": None, "Tender 1": "Fine."}


def test_empty_answers_are_not_stored(setup) -> None:
    engine, run_id = setup

    async def summarizer(title: str, text: str) -> PostingInfo:
        return PostingInfo(summary="   \n ")

    assert asyncio.run(summarize_matches(engine, run_id, "keywords", summarizer)) == 0


def test_clean_summary_is_one_capped_paragraph() -> None:
    assert clean_summary("a  b\n\nc") == "a b c"
    long = clean_summary("word " * 500)
    assert len(long) <= MAX_SUMMARY_CHARS + 3 and long.endswith("...") and "  " not in long


def test_set_listing_field_keeps_other_fields(setup) -> None:
    engine, run_id = setup
    (listing, _) = queries.matched_listings(engine, run_id)[0]
    queries.set_listing_field(engine, listing.id, "summary", "S")
    (updated, _) = [x for x in queries.matched_listings(engine, run_id) if x[0].id == listing.id][0]
    assert updated.fields == {"listed_date": "2026-10-09", "summary": "S"}


def test_summary_appears_in_html_and_text_reports(setup) -> None:
    engine, run_id = setup

    async def summarizer(title: str, text: str) -> PostingInfo:
        return PostingInfo(summary="The buyer wants <b>SAP</b> consulting.")

    asyncio.run(summarize_matches(engine, run_id, "keywords", summarizer))
    report = build_report(engine, run_id, "keywords")
    assert "The buyer wants &lt;b&gt;SAP&lt;/b&gt; consulting." in render_html(report)  # escaped
    assert "The buyer wants <b>SAP</b> consulting." in render_text(report)


def test_runtime_agents_load_nothing_from_disk_and_have_no_tools() -> None:
    options = isolated_options("system", 2)
    assert options.setting_sources == [] and options.tools == []
    assert options.env == {"ENABLE_CLAUDEAI_MCP_SERVERS": "false"}
    assert options.system_prompt == "system"


def test_at_most_n_summaries_run_at_the_same_time() -> None:
    engine = make_engine("sqlite://")
    run_id = queries.start_run(engine)
    for i in range(6):
        queries.add_listing(
            engine,
            Listing(site="demo", job_id=f"S{i}", url=f"https://x/S{i}", title=f"T{i}", posting_text="SAP work", text_hash=str(i)),
            run_id,
        )
    match_new_listings(engine, KeywordMatcher(["SAP"]), "keywords", "demo", run_id)
    running = peak = 0

    async def summarizer(title: str, text: str) -> PostingInfo:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return PostingInfo(summary=f"Summary of {title}")

    assert asyncio.run(summarize_matches(engine, run_id, "keywords", summarizer, workers=3)) == 6
    assert peak == 3  # really parallel, and never more than the limit
    assert all(m.summary for m in build_report(engine, run_id, "keywords").matches)


TEXT = "Bids must be received by the Contracting Authority\nno later than 2026-12-31 at 13:30 EST."


def answer(**kw) -> str:
    import json

    base = {"summary": "S", "how_to_apply": "Via the portal.", "closing_date": "2026-12-31",
            "closing_date_quote": "no later than 2026-12-31 at 13:30 EST", "closing_date_certain": True}
    return "```json\n" + json.dumps({**base, **kw}) + "\n```"  # agents sometimes add a code fence


def test_parse_reads_json_even_inside_a_code_fence_and_trusts_a_verified_date() -> None:
    info = parse_posting_info(answer(), TEXT)
    assert (info.summary, info.how_to_apply, info.closing_date, info.closing_unsure) == ("S", "Via the portal.", "2026-12-31", False)


def test_date_is_unsure_when_not_certain_quote_is_missing_from_text_or_date_is_bad() -> None:
    assert parse_posting_info(answer(closing_date_certain=False), TEXT).closing_unsure
    assert parse_posting_info(answer(closing_date_quote="due on 2026-12-31 maybe"), TEXT).closing_unsure  # invented quote
    assert parse_posting_info(answer(closing_date_quote=""), TEXT).closing_unsure
    bad = parse_posting_info(answer(closing_date="soon"), TEXT)
    assert bad.closing_date == "" and bad.closing_unsure


def test_unusable_answers_raise() -> None:
    with pytest.raises(ValueError):
        parse_posting_info("I could not read it", TEXT)


def test_closing_text_and_how_to_apply_reach_the_report(setup) -> None:
    engine, run_id = setup

    async def summarizer(title: str, text: str) -> PostingInfo:
        if title == "Tender 0":
            return PostingInfo(summary="S", how_to_apply="Email bids.", closing_date="2026-12-31", closing_unsure=False)
        if title == "Tender 1":
            return PostingInfo(summary="S", closing_date="2026-12-31")  # unsure by default
        return PostingInfo(summary="S")

    asyncio.run(summarize_matches(engine, run_id, "keywords", summarizer))
    shown = {m.title: (m.closing_date, m.how_to_apply) for m in build_report(engine, run_id, "keywords").matches}
    assert shown == {"Tender 0": ("2026-12-31", "Email bids."), "Tender 1": ("2026-12-31 (unsure, please verify)", None)}


def test_redo_reads_listings_that_were_already_read(setup) -> None:
    engine, run_id = setup
    calls: list[str] = []

    async def summarizer(title: str, text: str) -> PostingInfo:
        calls.append(title)
        return PostingInfo(summary=f"Read {len(calls)}")

    asyncio.run(summarize_matches(engine, run_id, "keywords", summarizer))
    assert asyncio.run(summarize_matches(engine, run_id, "keywords", summarizer)) == 0  # skipped by default
    assert asyncio.run(summarize_matches(engine, run_id, "keywords", summarizer, redo=True)) == 2
    assert len(calls) == 4
