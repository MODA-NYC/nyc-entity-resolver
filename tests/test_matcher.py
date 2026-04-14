"""End-to-end tests for the matching pipeline using a fixed fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from nycresolver.fetcher import CanonicalRecord, load_canonical_from_file
from nycresolver.matcher import (
    Matcher,
    MatchResult,
    TIER_HIGH,
    TIER_LOW,
    TIER_MEDIUM,
    confidence_tier,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "canonical_sample.json"


@pytest.fixture(scope="module")
def canonical() -> list[CanonicalRecord]:
    return load_canonical_from_file(FIXTURE_PATH)


@pytest.fixture(scope="module")
def matcher(canonical: list[CanonicalRecord]) -> Matcher:
    return Matcher(canonical)


def _result(matcher: Matcher, value: str) -> MatchResult:
    return matcher.match(value)


class TestExactMatches:
    def test_exact_canonical_name(self, matcher: Matcher):
        result = _result(matcher, "Department of Finance")
        assert result.matched_canonical_name == "Department of Finance"
        assert result.confidence_score == 100.0
        assert result.match_type == "exact_name"
        assert result.confidence_tier == "exact"
        assert not result.needs_review

    def test_case_insensitive_name(self, matcher: Matcher):
        result = _result(matcher, "department of finance")
        assert result.confidence_score == 100.0
        assert result.matched_canonical_name == "Department of Finance"

    def test_whitespace_tolerant(self, matcher: Matcher):
        result = _result(matcher, "  Department of  Finance ")
        assert result.confidence_score == 100.0

    def test_alphabetized_form(self, matcher: Matcher):
        result = _result(matcher, "Finance, Department of")
        assert result.matched_canonical_name == "Department of Finance"
        assert result.confidence_score == 100.0


class TestAcronymMatches:
    def test_primary_acronym(self, matcher: Matcher):
        result = _result(matcher, "DOF")
        assert result.matched_canonical_name == "Department of Finance"
        assert result.match_type == "exact_acronym"
        assert result.confidence_score == 100.0

    def test_acronym_lowercase(self, matcher: Matcher):
        result = _result(matcher, "dof")
        assert result.matched_canonical_name == "Department of Finance"

    def test_alternate_acronym(self, matcher: Matcher):
        result = _result(matcher, "DOS")
        assert result.matched_canonical_name == "Department of Sanitation"
        assert result.match_type == "exact_alternate_acronym"
        assert result.confidence_score == 100.0

    def test_acronym_for_record_without_alternate_acronym(self, matcher: Matcher):
        result = _result(matcher, "NYPD")
        assert result.matched_canonical_name == "Police Department"


class TestAlternateNameMatches:
    def test_alternate_name_exact(self, matcher: Matcher):
        result = _result(matcher, "NYC Sanitation")
        assert result.matched_canonical_name == "Department of Sanitation"
        assert result.match_type == "exact_alternate_name"
        assert result.confidence_score == 100.0

    def test_alternate_name_for_education(self, matcher: Matcher):
        result = _result(matcher, "NYC Public Schools")
        assert result.matched_canonical_name == "Department of Education"
        assert result.match_type == "exact_alternate_name"

    def test_long_alternate_name(self, matcher: Matcher):
        result = _result(matcher, "Fire Department of the City of New York")
        assert result.matched_canonical_name == "Fire Department"


class TestAbbreviationExpansion:
    def test_dept_shorthand(self, matcher: Matcher):
        result = _result(matcher, "Dept of Finance")
        assert result.matched_canonical_name == "Department of Finance"
        assert result.match_type == "abbreviation_expansion"
        assert result.confidence_score >= TIER_HIGH

    def test_dept_with_period(self, matcher: Matcher):
        result = _result(matcher, "Dept. of Finance")
        assert result.matched_canonical_name == "Department of Finance"
        assert result.match_type == "abbreviation_expansion"

    def test_reordered_and_abbreviated(self, matcher: Matcher):
        result = _result(matcher, "Finance, Dept of")
        assert result.matched_canonical_name == "Department of Finance"
        assert result.match_type == "abbreviation_expansion"
        assert result.confidence_score >= TIER_HIGH

    def test_heavy_abbreviation(self, matcher: Matcher):
        result = _result(matcher, "Dept of Citywide Admin Services")
        assert result.matched_canonical_name == "Department of Citywide Administrative Services"
        # "admin" expands to "administration"; the canonical uses
        # "administrative". The two forms are lexically similar but not
        # token-equal, so the match lands in the medium/high band rather
        # than triggering the abbreviation_expansion short-circuit.
        assert result.confidence_score >= TIER_MEDIUM

    def test_ampersand_treated_as_and(self, matcher: Matcher):
        result = _result(matcher, "Department of Health & Mental Hygiene")
        assert result.matched_canonical_name == "Department of Health and Mental Hygiene"
        assert result.confidence_score >= TIER_HIGH

    def test_loose_abbreviation_drops_stopword(self, matcher: Matcher):
        """``Finance Dept`` lacks the ``of`` — the stopword-stripped path
        should still surface it as a high-tier abbreviation expansion."""
        result = _result(matcher, "Finance Dept")
        assert result.matched_canonical_name == "Department of Finance"
        assert result.match_type == "abbreviation_expansion"
        assert result.confidence_score >= TIER_HIGH


class TestTypoTolerance:
    def test_single_letter_typo(self, matcher: Matcher):
        result = _result(matcher, "Deparment of Finance")
        assert result.matched_canonical_name == "Department of Finance"
        assert result.confidence_score >= TIER_MEDIUM

    def test_double_letter_typo_still_matches(self, matcher: Matcher):
        result = _result(matcher, "Departmnt of Finnce")
        assert result.matched_canonical_name == "Department of Finance"
        assert result.confidence_score >= TIER_LOW


class TestWordReordering:
    def test_alphabetized_variant_already_in_record(self, matcher: Matcher):
        result = _result(matcher, "Sanitation, Department of")
        assert result.matched_canonical_name == "Department of Sanitation"
        assert result.confidence_score == 100.0

    def test_partial_reorder_without_abbreviation(self, matcher: Matcher):
        result = _result(matcher, "Finance Department")
        assert result.matched_canonical_name == "Department of Finance"
        assert result.confidence_score >= TIER_HIGH


class TestNoMatch:
    def test_completely_unrelated_input(self, matcher: Matcher):
        result = _result(matcher, "Ministry of Silly Walks")
        assert not result.matched
        assert result.best is None
        assert result.match_type == "no_match"
        assert result.confidence_tier == "none"
        assert result.needs_review

    def test_empty_input(self, matcher: Matcher):
        result = _result(matcher, "")
        assert result.best is None
        assert result.match_type == "no_match"

    def test_whitespace_only_input(self, matcher: Matcher):
        result = _result(matcher, "   ")
        assert result.best is None

    def test_schroedinger_department(self, matcher: Matcher):
        result = _result(matcher, "Schrödinger's Department")
        # Sharing the word "Department" with a dozen canonical records
        # will produce a low-tier candidate (for human review), not an
        # auto-confident match.
        assert result.confidence_score < TIER_MEDIUM
        assert result.needs_review


class TestTieBreaking:
    def test_prefers_active_over_inactive_on_exact_tie(self):
        """When two records produce the same score, prefer the active one."""
        active = CanonicalRecord.from_row(
            {
                "record_id": "ACTIVE",
                "name": "Department of Example",
                "operational_status": "Active",
            }
        )
        inactive = CanonicalRecord.from_row(
            {
                "record_id": "INACTIVE",
                "name": "Department of Example",
                "operational_status": "Inactive",
            }
        )
        # Inactive listed first so a stable sort alone wouldn't prefer active.
        matcher = Matcher([inactive, active])
        result = matcher.match("Department of Example")
        assert result.best is not None
        assert result.best.record.record_id == "ACTIVE"
        assert result.confidence_score == 100.0

    def test_runners_up_populated(self, matcher: Matcher):
        """The runner-up list surfaces other plausible candidates."""
        result = _result(matcher, "Department of Finance")
        assert result.best is not None
        assert result.best.record.record_id == "NYC_GOID_TEST_001"
        # Other "Department of X" records should show up as runners-up.
        assert len(result.runners_up) > 0
        # The top match should not appear in runners_up.
        best_id = result.best.record.record_id
        assert all(r.record.record_id != best_id for r in result.runners_up)


class TestBatch:
    def test_batch_preserves_order(self, matcher: Matcher):
        inputs = ["DOF", "NYPD", "XYZ Agency"]
        results = matcher.batch(inputs)
        assert [r.input_value for r in results] == inputs
        assert results[0].matched_canonical_name == "Department of Finance"
        assert results[1].matched_canonical_name == "Police Department"
        assert results[2].best is None


class TestConfidenceTier:
    def test_tier_boundaries(self):
        assert confidence_tier(100) == "exact"
        assert confidence_tier(99.9) == "high"
        assert confidence_tier(85) == "high"
        assert confidence_tier(84.9) == "medium"
        assert confidence_tier(65) == "medium"
        assert confidence_tier(64.9) == "low"
        assert confidence_tier(45) == "low"
        assert confidence_tier(44.9) == "none"
        assert confidence_tier(0) == "none"


class TestMatcherConfiguration:
    def test_rejects_bad_weights(self, canonical: list[CanonicalRecord]):
        bad_weights = {
            "levenshtein": 0.5,
            "token_sort": 0.5,
            "token_sort_expanded": 0.5,
            "abbreviation_expanded": 0.5,
            "jaccard_meaningful": 0.5,
            "contains_bonus": 0.5,
        }
        with pytest.raises(ValueError):
            Matcher(canonical, weights=bad_weights)

    def test_requires_all_weights(self, canonical: list[CanonicalRecord]):
        with pytest.raises(ValueError):
            Matcher(canonical, weights={"levenshtein": 1.0})
