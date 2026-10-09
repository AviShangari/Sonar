from pathlib import Path

import pytest
import yaml

from sonar.matching.keywords import KeywordMatcher, load_keywords

ROOT = Path(__file__).resolve().parent.parent
LISTINGS = yaml.safe_load((Path(__file__).parent / "fixtures" / "listings.yaml").read_text(encoding="utf-8"))["listings"]


@pytest.fixture(scope="module")
def matcher() -> KeywordMatcher:
    return KeywordMatcher(load_keywords(ROOT / "config" / "keywords.yaml"))


@pytest.mark.parametrize("listing", LISTINGS, ids=[item["id"] for item in LISTINGS])
def test_listing_matches_expected_keywords(matcher: KeywordMatcher, listing: dict) -> None:
    result = matcher.match(listing["text"])
    assert {hit.keyword for hit in result.hits} == set(listing["expected"])
    assert result.matched == bool(listing["expected"])


def test_repeated_keyword_counts_every_occurrence(matcher: KeywordMatcher) -> None:
    (hit,) = matcher.match("SAP is used here. We run SAP for finance and sap for HR.").hits
    assert hit.count == 3


def test_snippet_shows_context_and_collapses_whitespace(matcher: KeywordMatcher) -> None:
    text = "x" * 100 + " Lead change\nmanagement now " + "y" * 100
    (hit,) = matcher.match(text).hits
    assert "change management" in hit.snippet
    assert "\n" not in hit.snippet
    assert hit.snippet.startswith("...") and hit.snippet.endswith("...")
