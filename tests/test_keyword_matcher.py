from pathlib import Path

import pytest
import yaml

from sonar.matching.keywords import KeywordMatcher, KeywordRule, load_context_terms, load_keywords

ROOT = Path(__file__).resolve().parent.parent
LISTINGS = yaml.safe_load((Path(__file__).parent / "fixtures" / "listings.yaml").read_text(encoding="utf-8"))["listings"]


@pytest.fixture(scope="module")
def matcher() -> KeywordMatcher:
    path = ROOT / "config" / "keywords.yaml"
    return KeywordMatcher(load_keywords(path), load_context_terms(path))


@pytest.mark.parametrize("listing", LISTINGS, ids=[item["id"] for item in LISTINGS])
def test_listing_matches_expected_keywords(matcher: KeywordMatcher, listing: dict) -> None:
    result = matcher.match(listing["text"])
    assert {hit.keyword for hit in result.hits} == set(listing["expected"])
    assert result.matched == bool(listing["expected"])


def test_repeated_keyword_counts_every_occurrence(matcher: KeywordMatcher) -> None:
    (hit,) = matcher.match("SAP is used here. We run SAP for finance and sap for HR.").hits
    assert hit.count == 3


def test_snippet_shows_context_and_collapses_whitespace(matcher: KeywordMatcher) -> None:
    text = "x" * 100 + " Lead change\nmanagement now, consulting " + "y" * 100
    (hit,) = matcher.match(text).hits
    assert "change management" in hit.snippet
    assert "\n" not in hit.snippet
    assert hit.snippet.startswith("...") and hit.snippet.endswith("...")


def test_ignored_phrase_hits_do_not_count_but_real_hits_do(matcher: KeywordMatcher) -> None:
    text = "Replace the legacy ERP with SAP S/4HANA. Bids go through SAP Ariba. SAP Business Network help: see SAP site."
    (hit,) = matcher.match(text).hits
    assert hit.keyword == "SAP"
    assert hit.count == 2  # S/4HANA and the bare "SAP site"; the two portal mentions are ignored
    assert "S/4HANA" in hit.snippet


def test_ignore_inside_is_whole_word_and_case_insensitive() -> None:
    rule = KeywordRule(keyword="SAP", ignore_inside=["SAP Ariba"])
    matcher = KeywordMatcher([rule])
    assert not matcher.match("log in to your sap   ARIBA account").matched  # case and line breaks do not matter
    assert matcher.match("SAP Aribaville is a place").matched  # "Aribaville" is not the phrase "SAP Ariba"


def test_load_keywords_reads_plain_and_ignore_inside_entries(tmp_path: Path) -> None:
    path = tmp_path / "keywords.yaml"
    path.write_text("keywords:\n  - Oracle\n  - keyword: SAP\n    ignore_inside: [SAP Ariba]\n  - '  '\n", encoding="utf-8")
    assert load_keywords(path) == [KeywordRule(keyword="Oracle"), KeywordRule(keyword="SAP", ignore_inside=["SAP Ariba"])]


def test_bare_microsoft_is_not_a_keyword() -> None:
    names = {rule.keyword for rule in load_keywords(ROOT / "config" / "keywords.yaml")}
    assert "Microsoft" not in names
    assert {"Microsoft Dynamics", "Dynamics 365", "Azure", "Power BI"} <= names


def test_generic_keyword_needs_a_context_term_whole_word() -> None:
    matcher = KeywordMatcher([KeywordRule(keyword="Project Management", needs_context=True)], ["cloud"])
    assert not matcher.match("Project Management for a bridge repair.").matched
    assert matcher.match("Project Management for a CLOUD migration.").matched
    assert not matcher.match("Project Management for a cloudy day festival.").matched  # whole word only


def test_needs_context_without_context_terms_is_a_config_error() -> None:
    with pytest.raises(ValueError, match="context_terms"):
        KeywordMatcher([KeywordRule(keyword="Project Management", needs_context=True)])


def test_real_keyword_file_gates_generic_keywords_but_not_specific_ones() -> None:
    path = ROOT / "config" / "keywords.yaml"
    rules = {rule.keyword: rule for rule in load_keywords(path)}
    assert all(rules[k].needs_context for k in ("Project Management", "change management", "Business Transformation", "Business intelligence"))
    assert not any(rules[k].needs_context for k in ("SAP", "Oracle", "Azure", "Dynamics 365", "RPA"))
    assert load_context_terms(path)
