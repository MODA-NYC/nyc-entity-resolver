"""Command-line interface for ``nycresolver``.

Usage::

    nycresolver INPUT.csv --column AGENCY_NAME [OPTIONS]

The input file is CSV or TSV. One row is produced in the crosswalk per
input row, preserving order.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import IO, Iterable, Iterator, Optional, Sequence, TextIO

from nycresolver import __version__
from nycresolver.export import summarize, write_crosswalk
from nycresolver.fetcher import (
    DEFAULT_DATASET_ID,
    DEFAULT_TTL_SECONDS,
    SocrataError,
    load_canonical,
    load_canonical_from_file,
)
from nycresolver.matcher import TIER_LOW, Matcher, MatchResult


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nycresolver",
        description=(
            "Reconcile NYC agency names against the canonical NYC "
            "Governance Organizations dataset."
        ),
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to a CSV or TSV file containing the column to match.",
    )
    parser.add_argument(
        "-c",
        "--column",
        required=True,
        help="Name of the column whose values should be matched.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Path to write the crosswalk (default: stdout).",
    )
    parser.add_argument(
        "-t",
        "--threshold",
        type=float,
        default=None,
        help=(
            "Drop rows from the output whose confidence score is below "
            "this value (0-100)."
        ),
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=TIER_LOW,
        help=(
            "Confidence score below which an input is treated as a no-match "
            "and reported with an empty canonical name. Default: 45."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("csv", "tsv"),
        default="csv",
        help="Output format (default: csv).",
    )
    parser.add_argument(
        "--input-format",
        choices=("auto", "csv", "tsv"),
        default="auto",
        help="Input file format (default: auto-detect).",
    )
    parser.add_argument(
        "--canonical-file",
        type=Path,
        default=None,
        help=(
            "Load canonical records from a local JSON file instead of the "
            "Socrata API. Useful for tests and offline runs."
        ),
    )
    parser.add_argument(
        "--dataset-id",
        default=DEFAULT_DATASET_ID,
        help=f"Socrata dataset ID (default: {DEFAULT_DATASET_ID}).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force a refetch of the canonical dataset.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable local caching entirely (implies --refresh).",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=DEFAULT_TTL_SECONDS,
        help=f"Cache TTL in seconds (default: {DEFAULT_TTL_SECONDS}).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Override the local cache directory.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the end-of-run summary on stderr.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.input.exists():
        parser.error(f"input file not found: {args.input}")

    try:
        canonical = _load_canonical(args)
    except SocrataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    matcher = Matcher(canonical, min_score=args.min_score)

    try:
        input_values = list(_read_input_column(args.input, args.column, args.input_format))
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: cannot read input file: {exc}", file=sys.stderr)
        return 2

    results: list[MatchResult] = matcher.batch(input_values)

    delimiter = "\t" if args.format == "tsv" else ","
    written = write_crosswalk(
        results,
        output=args.output,
        threshold=args.threshold,
        delimiter=delimiter,
    )

    if not args.quiet:
        _write_summary(results, written, canonical_count=len(canonical))
    return 0


def _load_canonical(args: argparse.Namespace):
    if args.canonical_file:
        return load_canonical_from_file(args.canonical_file)
    ttl = 0 if args.no_cache else args.ttl
    refresh = args.refresh or args.no_cache
    return load_canonical(
        dataset_id=args.dataset_id,
        refresh=refresh,
        ttl_seconds=ttl,
        cache_dir=args.cache_dir,
    )


def _read_input_column(
    path: Path, column: str, fmt: str
) -> Iterator[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = _build_reader(handle, fmt)
        if reader.fieldnames is None or column not in reader.fieldnames:
            available = ", ".join(reader.fieldnames or [])
            raise KeyError(
                f"column '{column}' not found in input. Available columns: {available}"
            )
        for row in reader:
            value = row.get(column)
            yield (value or "").strip()


def _build_reader(handle: TextIO, fmt: str) -> csv.DictReader:
    if fmt == "csv":
        return csv.DictReader(handle)
    if fmt == "tsv":
        return csv.DictReader(handle, delimiter="\t")
    # auto-detect
    sample = handle.read(4096)
    handle.seek(0)
    if not sample:
        return csv.DictReader(handle)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return csv.DictReader(handle, dialect=dialect)
    except csv.Error:
        return csv.DictReader(handle)


def _write_summary(
    results: Sequence[MatchResult], written: int, canonical_count: int
) -> None:
    summary = summarize(results)
    lines = [
        f"[nycresolver] canonical records loaded: {canonical_count}",
        f"[nycresolver] inputs scored: {summary['total']}",
        f"[nycresolver]   exact:  {summary.get('exact', 0)}",
        f"[nycresolver]   high:   {summary.get('high', 0)}",
        f"[nycresolver]   medium: {summary.get('medium', 0)}",
        f"[nycresolver]   low:    {summary.get('low', 0)}",
        f"[nycresolver]   none:   {summary.get('none', 0)}",
        f"[nycresolver] rows written: {written}",
    ]
    for line in lines:
        print(line, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
