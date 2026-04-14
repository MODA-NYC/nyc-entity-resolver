"""CSV export for match results.

The crosswalk format is deliberately flat — one row per input value, with
enough context to decide whether to trust a match at a glance:

    input_value, matched_canonical_name, matched_acronym, matched_record_id,
    matched_variant, confidence_score, confidence_tier, match_type,
    needs_review
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import IO, Iterable, Iterator, Mapping, Optional, Sequence

from nycresolver.matcher import MatchResult

CROSSWALK_COLUMNS: tuple[str, ...] = (
    "input_value",
    "matched_canonical_name",
    "matched_acronym",
    "matched_record_id",
    "matched_variant",
    "confidence_score",
    "confidence_tier",
    "match_type",
    "needs_review",
)


def result_to_row(result: MatchResult) -> dict[str, str]:
    """Flatten a single :class:`MatchResult` into CSV-ready cells."""
    score = f"{result.confidence_score:.2f}" if result.best else ""
    return {
        "input_value": result.input_value,
        "matched_canonical_name": result.matched_canonical_name,
        "matched_acronym": result.matched_acronym,
        "matched_record_id": result.matched_record_id,
        "matched_variant": result.best.matched_variant if result.best else "",
        "confidence_score": score,
        "confidence_tier": result.confidence_tier,
        "match_type": result.match_type,
        "needs_review": "true" if result.needs_review else "false",
    }


def results_to_rows(results: Iterable[MatchResult]) -> Iterator[dict[str, str]]:
    for result in results:
        yield result_to_row(result)


def write_crosswalk(
    results: Iterable[MatchResult],
    output: Optional[Path] = None,
    *,
    threshold: Optional[float] = None,
    delimiter: str = ",",
) -> int:
    """Write a crosswalk CSV to ``output`` (or stdout if ``None``).

    Parameters
    ----------
    threshold:
        If set, drop rows whose confidence score is below this value.
    delimiter:
        Cell delimiter — use ``"\\t"`` for TSV.

    Returns
    -------
    Row count written (excluding the header).
    """
    filtered = _apply_threshold(results, threshold)
    if output is None:
        return _write_rows(sys.stdout, filtered, delimiter)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        return _write_rows(handle, filtered, delimiter)


def _apply_threshold(
    results: Iterable[MatchResult], threshold: Optional[float]
) -> Iterator[MatchResult]:
    if threshold is None:
        yield from results
        return
    for result in results:
        if result.best is not None and result.best.confidence_score >= threshold:
            yield result


def _write_rows(
    handle: IO[str],
    results: Iterable[MatchResult],
    delimiter: str,
) -> int:
    writer = csv.DictWriter(
        handle, fieldnames=list(CROSSWALK_COLUMNS), delimiter=delimiter
    )
    writer.writeheader()
    written = 0
    for row in results_to_rows(results):
        writer.writerow(row)
        written += 1
    return written


def summarize(results: Sequence[MatchResult]) -> Mapping[str, int]:
    """Return a tier-name → count summary for batch reporting."""
    summary: dict[str, int] = {
        "exact": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "none": 0,
        "total": 0,
    }
    for result in results:
        summary["total"] += 1
        summary[result.confidence_tier] = summary.get(result.confidence_tier, 0) + 1
    return summary
