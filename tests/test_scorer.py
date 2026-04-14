"""Unit tests for the individual scoring functions."""

from __future__ import annotations

import pytest

from nycresolver.scorer import (
    abbreviation_expanded_ratio,
    acronym_match,
    contains_bonus,
    exact,
    generated_acronym,
    jaccard_meaningful,
    jaccard_tokens,
    levenshtein_ratio,
    normalize,
    normalize_expanded,
    sorted_normalized_expanded,
    token_sort_expanded_ratio,
    token_sort_ratio,
    tokens,
)


class TestNormalize:
    def test_lowercases_and_strips(self):
        assert normalize("  Department of Finance  ") == "department of finance"

    def test_drops_punctuation(self):
        assert normalize("Dept. of Finance") == "dept of finance"
        assert normalize("Finance, Dept. of") == "finance dept of"

    def test_collapses_whitespace(self):
        assert normalize("Dept  of\tFinance") == "dept of finance"

    def test_ampersand_becomes_and(self):
        assert normalize("Health & Mental Hygiene") == "health and mental hygiene"

    def test_empty_returns_empty(self):
        assert normalize("") == ""
        assert normalize("   ") == ""


class TestNormalizeExpanded:
    def test_expands_known_abbreviations(self):
        assert normalize_expanded("Dept of Finance") == "department of finance"
        assert normalize_expanded("Fin Dept") == "finance department"

    def test_does_not_expand_unknown_words(self):
        assert normalize_expanded("Fungibility Bureau") == "fungibility bureau"

    def test_case_insensitive(self):
        assert normalize_expanded("DEPT OF FINANCE") == "department of finance"


class TestExact:
    def test_exact_match(self):
        assert exact("Department of Finance", "Department of Finance") == 1.0

    def test_case_insensitive(self):
        assert exact("department of finance", "Department of Finance") == 1.0

    def test_whitespace_insensitive(self):
        assert exact("Department of Finance", "  Department  of  Finance  ") == 1.0

    def test_mismatch(self):
        assert exact("Department of Finance", "Department of Sanitation") == 0.0

    def test_empty_strings_do_not_match(self):
        assert exact("", "") == 0.0


class TestLevenshteinRatio:
    def test_identical(self):
        assert levenshtein_ratio("Finance", "Finance") == 1.0

    def test_single_letter_typo(self):
        ratio = levenshtein_ratio("Deparment of Finance", "Department of Finance")
        assert ratio > 0.90

    def test_completely_different(self):
        assert levenshtein_ratio("alpha", "omega") < 0.5

    def test_empty_vs_nonempty(self):
        assert levenshtein_ratio("", "Finance") == 0.0


class TestAbbreviationExpandedRatio:
    def test_abbreviation_match_is_perfect(self):
        ratio = abbreviation_expanded_ratio("Dept of Finance", "Department of Finance")
        assert ratio == pytest.approx(1.0)

    def test_multi_abbreviation(self):
        ratio = abbreviation_expanded_ratio(
            "Dept of Envir Protection", "Department of Environmental Protection"
        )
        assert ratio == pytest.approx(1.0)


class TestTokenSortRatio:
    def test_reordered_words_full_score(self):
        ratio = token_sort_ratio("Department of Finance", "Finance, Department of")
        assert ratio == pytest.approx(1.0)

    def test_different_tokens(self):
        ratio = token_sort_ratio("Department of Finance", "Department of Sanitation")
        assert ratio < 1.0


class TestTokenSortExpandedRatio:
    def test_reordered_and_abbreviated(self):
        ratio = token_sort_expanded_ratio("Finance, Dept of", "Department of Finance")
        assert ratio == pytest.approx(1.0)


class TestSortedNormalizedExpanded:
    def test_produces_sorted_tokens(self):
        assert sorted_normalized_expanded("Finance, Dept of") == "department finance of"

    def test_equal_across_reorder_and_abbreviation(self):
        assert sorted_normalized_expanded(
            "Dept of Finance"
        ) == sorted_normalized_expanded("Finance, Department of")


class TestJaccard:
    def test_full_overlap(self):
        assert jaccard_tokens("Department of Finance", "Department of Finance") == 1.0

    def test_no_overlap(self):
        assert jaccard_tokens("Department of Finance", "XYZ Agency") == 0.0

    def test_partial_overlap(self):
        score = jaccard_tokens("Department of Finance", "Department of Sanitation")
        assert 0.0 < score < 1.0


class TestJaccardMeaningful:
    def test_strips_stopwords(self):
        score = jaccard_meaningful("Department of Finance", "Finance Department")
        assert score == pytest.approx(1.0)

    def test_expansion_before_comparison(self):
        score = jaccard_meaningful("Dept of Finance", "Finance Department")
        assert score == pytest.approx(1.0)


class TestGeneratedAcronym:
    def test_mayors_office_of_pensions(self):
        assert generated_acronym("Mayor's Office of Pensions and Investments") == "mopi"

    def test_skips_stopwords(self):
        assert generated_acronym("Department of Finance") == "df"

    def test_first_deputy_mayor(self):
        assert generated_acronym("First Deputy Mayor") == "fdm"


class TestAcronymMatch:
    def test_input_is_generated_acronym(self):
        assert acronym_match("MOPI", "Mayor's Office of Pensions and Investments") == 1.0

    def test_non_matching_returns_zero(self):
        assert acronym_match("XYZ", "Department of Finance") == 0.0


class TestContainsBonus:
    def test_substring_returns_ratio(self):
        bonus = contains_bonus("Finance", "Department of Finance")
        assert bonus > 0.0

    def test_non_substring_returns_zero(self):
        assert contains_bonus("Sanitation", "Department of Finance") == 0.0


class TestTokens:
    def test_tokens_of_punctuated(self):
        assert tokens("Finance, Dept. of") == ["finance", "dept", "of"]

    def test_empty(self):
        assert tokens("") == []
