"""The assistant's tools over a small real SQLite file. No network, no model calls."""

import asyncio
import json
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from sonar.assistant.safe import CLOSE_TAG, OPEN_TAG
from sonar.assistant.tools import MAX_LIMIT, NO_COST_NOTE, TEXT_CHUNK, TOOL_NAMES, AssistantTools, read_only_engine
from sonar.config import Settings
from sonar.db import queries
from sonar.db.tables import make_engine
from sonar.llm import ToolCall
from sonar.models import Decision, Listing


def hit(keyword: str, snippet: str) -> dict:
    return {"keyword": keyword, "count": 2, "snippet": snippet}


@pytest.fixture()
def db_url(tmp_path) -> str:
    url = f"sqlite:///{tmp_path / 'sonar.db'}"
    engine = make_engine(url)
    run = queries.start_run(engine)

    def add(site: str, job: str, title: str, **fields) -> int:
        listing = Listing(site=site, job_id=job, url=f"https://{site}.example/{job}", title=title, posting_text="full text " * 50,
                          text_hash=job, fields={"listed_date": "2026-10-20"})
        listing_id = queries.add_listing(engine, listing, run)
        for key, value in fields.items():  # the summary step writes these straight into the JSON column (a bool, a list)
            queries.set_listing_field(engine, listing_id, key, value)
        return listing_id

    def decide(listing_id: int, matcher: str, matched: bool | None, score=None, **details) -> None:
        queries.add_decision(engine, Decision(listing_id=listing_id, run_id=run, matcher=matcher, matched=matched, score=score, details=details))

    sap = add("canadabuys", "J1", "SAP migration RFP", summary="Move finance to SAP S/4HANA.", closing_date="2026-11-05",
              closing_unsure=False, department="Public Works", unverified=[])
    decide(sap, "keywords", True, hits=[hit("SAP", "migrate to SAP S/4HANA now")])
    cloud = add("ontario", "J2", "Cloud staffing", summary="Azure contractors.", closing_date="2026-12-01", closing_unsure=True, unverified=[])
    decide(cloud, "keywords", True, hits=[hit("Azure", "Azure landing zone")])
    decide(add("canadabuys", "J3", "Road paving"), "keywords", False, hits=[])
    nodate = add("ariba", "J4", "Procurement portal upgrade", summary="No date given.")
    decide(nodate, "keywords", True, hits=[hit("portal", "upgrade the portal")])
    decide(nodate, "jev", True, score=0.52, needs_review=True)
    jev_only = add("merx", "J5", "Unscored thing")
    decide(jev_only, "jev", None, error="Jev timed out")
    queries.finish_run(engine, run, "partial", "ontario: OpenFailures: boom")
    return url


def make_tools(db_url: str, **settings) -> AssistantTools:
    return AssistantTools(read_only_engine(db_url), Settings(matcher="keywords", **settings), {"ariba": "rfp@example.com"})


def call(tools: AssistantTools, name: str, **args) -> str:
    return asyncio.run(tools.run(ToolCall(id="1", name=name, arguments=json.dumps(args))))


def payload(result: str) -> dict:
    assert result.startswith(OPEN_TAG + "\n") and result.endswith("\n" + CLOSE_TAG)
    return json.loads(result.removeprefix(OPEN_TAG + "\n").removesuffix("\n" + CLOSE_TAG))


def test_there_are_exactly_these_tools(db_url) -> None:
    assert TOOL_NAMES == [
        "list_recent_matches", "search_matches", "get_posting", "explain_match", "read_posting_text", "last_run_status",
        "pdf_options", "make_pdf",
    ]
    assert set(make_tools(db_url).handlers) == set(TOOL_NAMES)


def test_list_recent_matches_shows_only_matches_newest_first(db_url) -> None:
    data = payload(call(make_tools(db_url), "list_recent_matches"))
    assert data["total_matches"] == 3
    assert [m["title"] for m in data["matches"]] == ["Procurement portal upgrade", "Cloud staffing", "SAP migration RFP"]
    first = data["matches"][0]
    assert first["account"] == "rfp@example.com" and first["closing_date"] is None
    assert "posting_text" not in json.dumps(data) and "full text" not in json.dumps(data)  # list results stay short


def test_the_limit_is_clamped(db_url) -> None:
    tools = make_tools(db_url)
    assert payload(call(tools, "list_recent_matches", limit=1))["shown"] == 1
    assert payload(call(tools, "list_recent_matches", limit=0))["shown"] == 1
    assert payload(call(tools, "list_recent_matches", limit=10_000))["shown"] <= MAX_LIMIT


def test_search_by_site_and_keyword(db_url) -> None:
    tools = make_tools(db_url)
    assert [m["title"] for m in payload(call(tools, "search_matches", site="CanadaBuys"))["matches"]] == ["SAP migration RFP"]
    assert [m["title"] for m in payload(call(tools, "search_matches", keyword="azure"))["matches"]] == ["Cloud staffing"]
    assert [m["title"] for m in payload(call(tools, "search_matches", keyword="s/4hana"))["matches"]] == ["SAP migration RFP"]  # summary
    assert payload(call(tools, "search_matches", keyword="nonexistent"))["found"] == 0


def test_search_by_closing_date_orders_soonest_first_and_counts_undated(db_url) -> None:
    tools = make_tools(db_url)
    data = payload(call(tools, "search_matches", closing_after="2026-11-01"))
    assert [m["title"] for m in data["matches"]] == ["SAP migration RFP", "Cloud staffing"]
    assert data["left_out_no_closing_date"] == 1
    data = payload(call(tools, "search_matches", closing_before="2026-11-30"))
    assert [m["title"] for m in data["matches"]] == ["SAP migration RFP"]
    assert payload(call(tools, "search_matches", closing_after="2026-11-06", closing_before="2026-11-30"))["found"] == 0


def test_search_by_first_seen_date(db_url) -> None:
    tools = make_tools(db_url)
    today = tools.settings.today()
    assert payload(call(tools, "search_matches", found_since=(today - timedelta(days=1)).isoformat()))["found"] == 3
    assert payload(call(tools, "search_matches", found_since=(today + timedelta(days=2)).isoformat()))["found"] == 0


def test_get_posting_gives_summary_fields_and_hits_but_not_the_page_text(db_url) -> None:
    tools = make_tools(db_url)
    data = payload(call(tools, "get_posting", listing_id=1))
    assert data["title"] == "SAP migration RFP" and data["summary"] == "Move finance to SAP S/4HANA."
    assert data["closing_date"] == "2026-11-05" and data["department"] == "Public Works"
    assert data["keyword_hits"] == [{"matcher": "keywords", "keyword": "SAP", "count": 2, "snippet": "migrate to SAP S/4HANA now"}]
    assert data["matched_by"] == ["keywords"]
    assert "full text" not in json.dumps(data)
    assert payload(call(tools, "get_posting", listing_id=2))["closing_date"] == "2026-12-01 (unsure, please verify)"


def test_a_missing_posting_is_a_plain_error(db_url) -> None:
    assert call(make_tools(db_url), "get_posting", listing_id=999).startswith("ERROR: there is no posting")
    assert call(make_tools(db_url), "read_posting_text", listing_id=999).startswith("ERROR: there is no posting")
    assert call(make_tools(db_url), "explain_match", listing_id=999).startswith("ERROR: there is no posting")


def test_explain_a_keyword_match(db_url) -> None:
    data = payload(call(make_tools(db_url), "explain_match", listing_id=1))
    (decision,) = data["decisions"]
    assert decision["matcher"] == "keywords" and decision["matched"] is True
    assert "whole word" in decision["reason"] and decision["keyword_hits"][0]["keyword"] == "SAP"


def test_explain_a_jev_match_uses_the_thresholds_from_settings(db_url) -> None:
    data = payload(call(make_tools(db_url, jev_match_threshold=0.7, jev_review_threshold=0.2), "explain_match", listing_id=4))
    keyword, jev = data["decisions"]
    assert keyword["matcher"] == "keywords" and jev["matcher"] == "jev"
    assert jev["score"] == 0.52 and jev["needs_review"] is True
    assert "0.7" in jev["reason"] and "0.2" in jev["reason"] and "not reasons" in jev["reason"]


def test_explain_an_unscored_posting_says_it_will_be_retried(db_url) -> None:
    (decision,) = payload(call(make_tools(db_url), "explain_match", listing_id=5))["decisions"]
    assert decision["matched"] is None and "Jev timed out" in decision["reason"] and "tries again" in decision["reason"]


def test_the_current_matcher_decides_what_a_match_is(db_url) -> None:
    jev_tools = AssistantTools(read_only_engine(db_url), Settings(matcher="jev"))
    data = payload(call(jev_tools, "list_recent_matches"))
    assert [m["title"] for m in data["matches"]] == ["Procurement portal upgrade"]
    assert data["matches"][0]["score"] == 0.52 and data["matches"][0]["needs_review"] is True


def test_last_run_status_reports_what_is_stored_and_says_cost_is_not(db_url) -> None:
    data = payload(call(make_tools(db_url), "last_run_status"))
    assert data["status"] == "partial" and data["notes"] == "ontario: OpenFailures: boom"
    assert (data["new_postings_stored"], data["scored"], data["matches"]) == (5, 4, 3)
    assert data["total_cost"] == NO_COST_NOTE and data["matcher_cost_usd"] == 0
    assert data["started"].endswith(("EDT", "EST"))  # shown in the business time zone


def test_last_run_status_on_an_empty_database(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    make_engine(url)
    assert payload(call(make_tools(url), "last_run_status"))["last_run"] is None


def test_matcher_cost_is_summed_from_the_decisions(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'cost.db'}"
    engine = make_engine(url)
    run = queries.start_run(engine)
    for n, cost in enumerate([0.00002, 0.00003]):
        listing = queries.add_listing(engine, Listing(site="s", job_id=str(n), url="https://s.example", posting_text="t", text_hash=str(n)), run)
        queries.add_decision(engine, Decision(listing_id=listing, run_id=run, matcher="jev", matched=False, details={"cost_usd": cost}))
    assert payload(call(make_tools(url), "last_run_status"))["matcher_cost_usd"] == 0.00005


def test_read_posting_text_returns_the_stored_text_in_chunks(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'long.db'}"
    engine = make_engine(url)
    run = queries.start_run(engine)
    body = "Line one\n\n\n\nLine   two\n" + "x" * (TEXT_CHUNK * 2)
    queries.add_listing(engine, Listing(site="s", job_id="1", url="https://s.example", title="T", posting_text=body, text_hash="h"), run)
    tools = make_tools(url)
    first = payload(call(tools, "read_posting_text", listing_id=1))
    assert first["total_chars"] == len(body) and first["offset"] == 0 and first["next_offset"] == TEXT_CHUNK
    assert first["text"].startswith("Line one\n\nLine two\nx") and len(first["text"]) <= TEXT_CHUNK  # line breaks kept, blank runs folded
    second = payload(call(tools, "read_posting_text", listing_id=1, offset=first["next_offset"]))
    assert second["text"].startswith("x") and second["next_offset"] == TEXT_CHUNK * 2
    last = payload(call(tools, "read_posting_text", listing_id=1, offset=second["next_offset"]))
    assert last["next_offset"] is None and len(last["text"]) == len(body) - TEXT_CHUNK * 2
    assert payload(call(tools, "read_posting_text", listing_id=1, offset=10**9))["text"] == ""


def test_read_posting_text_rejects_a_negative_offset(db_url) -> None:
    assert call(make_tools(db_url), "read_posting_text", listing_id=1, offset=-5).startswith("ERROR: bad arguments")


def test_the_database_connection_cannot_write(db_url) -> None:
    engine = read_only_engine(db_url)
    with engine.begin() as conn, pytest.raises(OperationalError):
        conn.execute(text("DELETE FROM listings"))
    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM listings")).scalar() == 5


def test_a_missing_database_is_reported_not_created(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        read_only_engine(f"sqlite:///{tmp_path / 'nope.db'}")
    assert not (tmp_path / "nope.db").exists()
    with pytest.raises(ValueError):
        read_only_engine("postgresql://x/y")
