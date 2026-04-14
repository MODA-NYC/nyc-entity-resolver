"""Matching pipeline for NYC agency identifiers.

Given a raw input string, the :class:`Matcher` iterates over every canonical
record and scores it using a layered strategy:

1. **Exact** match against any canonical name variant or acronym → 100.
2. **Abbreviation expansion** — expand common NYC gov abbreviations and
   recheck exact equality → 97.
3. **Generated acronym** — derive an acronym from the canonical name by
   taking first letters of significant words; if the input matches that
   derived acronym → 92.
4. **Fuzzy composite** — weighted combination of Levenshtein, token-sort
   ratio, Jaccard, and abbreviation-expanded Levenshtein, plus an additive
   substring-containment bonus.

The highest-scoring candidate is returned along with the variant that
triggered the match, the match type, and a confidence tier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from nycresolver.fetcher import CanonicalRecord, load_canonical
from nycresolver.scorer import (
    abbreviation_expanded_ratio,
    contains_bonus,
    generated_acronym,
    jaccard_meaningful,
    levenshtein_ratio,
    normalize,
    normalize_expanded,
    sorted_meaningful_expanded,
    sorted_normalized_expanded,
    token_sort_expanded_ratio,
    token_sort_ratio,
)

# Score floors for each confidence tier (out of 100).
TIER_EXACT = 100
TIER_HIGH = 85
TIER_MEDIUM = 65
TIER_LOW = 45

# Canonical composite weights. Must sum to 1.0.
#
# The strongest signal is ``token_sort_expanded`` — it catches reordered
# *and* abbreviated variants in one go. Raw ``levenshtein`` and
# ``abbreviation_expanded`` are kept for typo tolerance; ``jaccard_meaningful``
# rewards overlap of significant words; ``contains_bonus`` gives partial
# credit when one normalized form is a substring of the other.
DEFAULT_WEIGHTS: Mapping[str, float] = {
    "levenshtein": 0.20,
    "token_sort": 0.15,
    "token_sort_expanded": 0.30,
    "abbreviation_expanded": 0.15,
    "jaccard_meaningful": 0.10,
    "contains_bonus": 0.10,
}

# Fixed scores for non-fuzzy match rules. Exact matches are 100; the other
# two sit a bit below to reflect the extra inference involved.
SCORE_ABBREVIATION_EXPANSION = 97.0
# Like abbreviation expansion, but after dropping function words too (e.g.
# "Finance Dept" ↔ "Department of Finance"). Slightly lower because we've
# discarded more information.
SCORE_ABBREVIATION_EXPANSION_LOOSE = 95.0
SCORE_GENERATED_ACRONYM = 92.0


@dataclass(frozen=True)
class Match:
    """A single candidate match against one canonical record."""

    record: CanonicalRecord
    matched_variant: str
    confidence_score: float
    match_type: str

    @property
    def confidence_tier(self) -> str:
        return confidence_tier(self.confidence_score)


@dataclass(frozen=True)
class MatchResult:
    """Final result for a single input value."""

    input_value: str
    best: Optional[Match]
    runners_up: tuple[Match, ...] = ()

    @property
    def matched(self) -> bool:
        return self.best is not None and self.best.confidence_score >= TIER_LOW

    @property
    def confidence_score(self) -> float:
        return self.best.confidence_score if self.best else 0.0

    @property
    def confidence_tier(self) -> str:
        if not self.best:
            return "none"
        return confidence_tier(self.best.confidence_score)

    @property
    def matched_canonical_name(self) -> str:
        return self.best.record.name if self.best else ""

    @property
    def matched_acronym(self) -> str:
        return self.best.record.acronym if self.best else ""

    @property
    def matched_record_id(self) -> str:
        return self.best.record.record_id if self.best else ""

    @property
    def match_type(self) -> str:
        return self.best.match_type if self.best else "no_match"

    @property
    def needs_review(self) -> bool:
        """True if the match is missing or below the high-confidence tier."""
        if not self.best:
            return True
        return self.best.confidence_score < TIER_HIGH


def confidence_tier(score: float) -> str:
    """Return the confidence-tier label for a numeric score."""
    if score >= TIER_EXACT:
        return "exact"
    if score >= TIER_HIGH:
        return "high"
    if score >= TIER_MEDIUM:
        return "medium"
    if score >= TIER_LOW:
        return "low"
    return "none"


@dataclass
class _IndexedVariant:
    original: str
    normalized: str
    normalized_expanded: str
    sorted_expanded: str
    sorted_meaningful: str
    derived_acronym: str
    kind: str  # "name", "alphabetized", or "alternate_name"


@dataclass
class _IndexedRecord:
    record: CanonicalRecord
    variants: list[_IndexedVariant]
    normalized_acronyms: list[tuple[str, str]]  # (normalized, kind)


def _index_record(record: CanonicalRecord) -> _IndexedRecord:
    variants: list[_IndexedVariant] = []

    def _add(original: str, kind: str) -> None:
        norm = normalize(original)
        if not norm:
            return
        for existing in variants:
            if existing.normalized == norm:
                return
        variants.append(
            _IndexedVariant(
                original=original,
                normalized=norm,
                normalized_expanded=normalize_expanded(original),
                sorted_expanded=sorted_normalized_expanded(original),
                sorted_meaningful=sorted_meaningful_expanded(original),
                derived_acronym=generated_acronym(original),
                kind=kind,
            )
        )

    _add(record.name, "name")
    _add(record.name_alphabetized, "alphabetized")
    for alt in record.alternate_names:
        _add(alt, "alternate_name")

    acronym_entries: list[tuple[str, str]] = []
    if record.acronym:
        acronym_entries.append((normalize(record.acronym), "acronym"))
    for alt in record.alternate_acronyms:
        acronym_entries.append((normalize(alt), "alternate_acronym"))
    acronym_entries = [(a, k) for (a, k) in acronym_entries if a]

    return _IndexedRecord(record=record, variants=variants, normalized_acronyms=acronym_entries)


class Matcher:
    """Score an input value against a set of canonical records."""

    def __init__(
        self,
        records: Sequence[CanonicalRecord],
        *,
        weights: Optional[Mapping[str, float]] = None,
        min_score: float = TIER_LOW,
        top_k_runners_up: int = 2,
    ) -> None:
        self._records = tuple(records)
        self._index = tuple(_index_record(r) for r in records)
        self._weights = dict(weights or DEFAULT_WEIGHTS)
        self._min_score = float(min_score)
        self._top_k_runners_up = int(top_k_runners_up)
        self._validate_weights()

    def _validate_weights(self) -> None:
        required = set(DEFAULT_WEIGHTS.keys())
        missing = required - set(self._weights.keys())
        if missing:
            raise ValueError(f"weights missing required keys: {sorted(missing)}")
        total = sum(self._weights[k] for k in required)
        if not (0.99 <= total <= 1.01):
            raise ValueError(f"composite weights must sum to 1.0 (got {total:.3f})")

    @property
    def records(self) -> tuple[CanonicalRecord, ...]:
        return self._records

    def match(self, input_value: str) -> MatchResult:
        """Return the best match for a single input value."""
        if input_value is None:
            return MatchResult(input_value="", best=None)
        normalized_input = normalize(input_value)
        if not normalized_input:
            return MatchResult(input_value=input_value, best=None)

        sorted_expanded_input = sorted_normalized_expanded(input_value)
        sorted_meaningful_input = sorted_meaningful_expanded(input_value)
        compact_input = normalized_input.replace(" ", "")

        candidates: list[Match] = []
        for indexed in self._index:
            candidate = self._score_record(
                indexed,
                normalized_input,
                sorted_expanded_input,
                sorted_meaningful_input,
                compact_input,
                input_value,
            )
            if candidate is not None:
                candidates.append(candidate)

        if not candidates:
            return MatchResult(input_value=input_value, best=None)

        candidates.sort(
            key=lambda c: (
                c.confidence_score,
                _operational_status_rank(c.record),
            ),
            reverse=True,
        )
        best = candidates[0]
        if best.confidence_score < self._min_score:
            return MatchResult(
                input_value=input_value,
                best=None,
                runners_up=tuple(candidates[: self._top_k_runners_up]),
            )
        runners_up = tuple(candidates[1 : 1 + self._top_k_runners_up])
        return MatchResult(input_value=input_value, best=best, runners_up=runners_up)

    def batch(self, inputs: Iterable[str]) -> list[MatchResult]:
        """Match a collection of input values."""
        return [self.match(value) for value in inputs]

    def _score_record(
        self,
        indexed: _IndexedRecord,
        normalized_input: str,
        sorted_expanded_input: str,
        sorted_meaningful_input: str,
        compact_input: str,
        raw_input: str,
    ) -> Optional[Match]:
        best_score = 0.0
        best_variant = ""
        best_match_type = ""

        # Canonical acronym lookup — exact match wins at 100.
        for normalized_acronym, kind in indexed.normalized_acronyms:
            if normalized_acronym == normalized_input or normalized_acronym == compact_input:
                match_type = (
                    "exact_acronym" if kind == "acronym" else "exact_alternate_acronym"
                )
                return Match(
                    record=indexed.record,
                    matched_variant=_display_acronym(indexed.record, kind),
                    confidence_score=100.0,
                    match_type=match_type,
                )

        # Walk each name variant and take the strongest signal.
        for variant in indexed.variants:
            if variant.normalized == normalized_input:
                match_type = {
                    "name": "exact_name",
                    "alphabetized": "exact_alphabetized",
                    "alternate_name": "exact_alternate_name",
                }[variant.kind]
                return Match(
                    record=indexed.record,
                    matched_variant=variant.original,
                    confidence_score=100.0,
                    match_type=match_type,
                )

            if (
                variant.sorted_expanded
                and variant.sorted_expanded == sorted_expanded_input
            ):
                score = SCORE_ABBREVIATION_EXPANSION
                if score > best_score:
                    best_score = score
                    best_variant = variant.original
                    best_match_type = "abbreviation_expansion"
                continue

            if (
                variant.sorted_meaningful
                and sorted_meaningful_input
                and variant.sorted_meaningful == sorted_meaningful_input
            ):
                score = SCORE_ABBREVIATION_EXPANSION_LOOSE
                if score > best_score:
                    best_score = score
                    best_variant = variant.original
                    best_match_type = "abbreviation_expansion"
                continue

            if variant.derived_acronym and variant.derived_acronym == compact_input:
                score = SCORE_GENERATED_ACRONYM
                if score > best_score:
                    best_score = score
                    best_variant = variant.original
                    best_match_type = "generated_acronym"
                continue

            composite = self._composite_score(raw_input, variant.original)
            score_100 = composite * 100.0
            if score_100 > best_score:
                best_score = score_100
                best_variant = variant.original
                best_match_type = "fuzzy"

        if best_score <= 0:
            return None
        return Match(
            record=indexed.record,
            matched_variant=best_variant,
            confidence_score=round(best_score, 2),
            match_type=best_match_type,
        )

    def _composite_score(self, a: str, b: str) -> float:
        w = self._weights
        score = (
            w["levenshtein"] * levenshtein_ratio(a, b)
            + w["token_sort"] * token_sort_ratio(a, b)
            + w["token_sort_expanded"] * token_sort_expanded_ratio(a, b)
            + w["abbreviation_expanded"] * abbreviation_expanded_ratio(a, b)
            + w["jaccard_meaningful"] * jaccard_meaningful(a, b)
            + w["contains_bonus"] * contains_bonus(a, b)
        )
        return max(0.0, min(1.0, score))


def _operational_status_rank(record: CanonicalRecord) -> int:
    """Tie-breaker: prefer active records over inactive/dissolved ones."""
    status = record.operational_status.strip().lower()
    if status == "active":
        return 2
    if status:
        return 1
    return 0


def _display_acronym(record: CanonicalRecord, kind: str) -> str:
    if kind == "acronym":
        return record.acronym
    return ", ".join(record.alternate_acronyms)


def build_matcher(
    *,
    dataset_id: Optional[str] = None,
    refresh: bool = False,
    ttl_seconds: Optional[int] = None,
    cache_dir: Optional[str] = None,
    min_score: float = TIER_LOW,
) -> Matcher:
    """Convenience: fetch canonical records and return a ready Matcher.

    Uses :func:`nycresolver.fetcher.load_canonical` with the same cache
    semantics. Intended for quick scripting — the CLI builds its own
    Matcher directly so it can tune fetch behavior.
    """
    from pathlib import Path as _Path

    kwargs: dict = {"refresh": refresh}
    if dataset_id is not None:
        kwargs["dataset_id"] = dataset_id
    if ttl_seconds is not None:
        kwargs["ttl_seconds"] = ttl_seconds
    if cache_dir is not None:
        kwargs["cache_dir"] = _Path(cache_dir)
    records = load_canonical(**kwargs)
    return Matcher(records, min_score=min_score)
