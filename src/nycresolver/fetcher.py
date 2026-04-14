"""Socrata client for the NYC Governance Organizations dataset.

Fetches the canonical dataset live from
``https://data.cityofnewyork.us/resource/t3jq-9nkf.json`` with pagination,
and caches the result locally with a TTL so batch operations don't hammer
the Socrata API. No dependencies beyond the standard library.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_DATASET_ID = "t3jq-9nkf"
DEFAULT_DOMAIN = "data.cityofnewyork.us"
DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24 hours
DEFAULT_PAGE_SIZE = 1000
DEFAULT_USER_AGENT = (
    "nycresolver/0.1 (+https://github.com/MODA-NYC/nyc-entity-resolver)"
)

# Alternate-name / alternate-acronym fields are delimited with a semicolon
# in the source dataset. Names themselves may contain commas (for example
# "Operations, Deputy Mayor for"), so comma-splitting would be unsafe.
_ALT_DELIMITER = ";"


@dataclass(frozen=True)
class CanonicalRecord:
    """A single NYC Governance Organization record.

    Only the fields that matter for entity resolution are surfaced as
    attributes; the full raw Socrata row is retained for downstream
    consumers that need it.
    """

    record_id: str
    name: str
    acronym: str = ""
    alternate_names: tuple[str, ...] = ()
    alternate_acronyms: tuple[str, ...] = ()
    name_alphabetized: str = ""
    organization_type: str = ""
    operational_status: str = ""
    reports_to: str = ""
    url: str = ""
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "CanonicalRecord":
        return cls(
            record_id=str(row.get("record_id", "")),
            name=str(row.get("name", "")),
            acronym=str(row.get("acronym", "")),
            alternate_names=_split_alternates(row.get("alternate_or_former_names")),
            alternate_acronyms=_split_alternates(row.get("alternate_or_former_acronyms")),
            name_alphabetized=str(row.get("name_alphabetized", "")),
            organization_type=str(row.get("organization_type", "")),
            operational_status=str(row.get("operational_status", "")),
            reports_to=str(row.get("reports_to", "")),
            url=str(row.get("url", "")),
            raw=dict(row),
        )

    @property
    def all_names(self) -> tuple[str, ...]:
        """Every name variant worth matching against: canonical + alphabetized + alternates."""
        variants: list[str] = []
        seen: set[str] = set()
        for candidate in (self.name, self.name_alphabetized, *self.alternate_names):
            stripped = candidate.strip()
            if stripped and stripped.lower() not in seen:
                seen.add(stripped.lower())
                variants.append(stripped)
        return tuple(variants)

    @property
    def all_acronyms(self) -> tuple[str, ...]:
        """Canonical acronym plus any historical/alternate acronyms."""
        variants: list[str] = []
        seen: set[str] = set()
        for candidate in (self.acronym, *self.alternate_acronyms):
            stripped = candidate.strip()
            if stripped and stripped.lower() not in seen:
                seen.add(stripped.lower())
                variants.append(stripped)
        return tuple(variants)


def _split_alternates(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return tuple(
        part.strip() for part in str(value).split(_ALT_DELIMITER) if part.strip()
    )


def default_cache_dir() -> Path:
    """Return the cache directory for canonical data.

    Honors ``NYCRESOLVER_CACHE_DIR`` if set, then ``XDG_CACHE_HOME``, then
    falls back to ``~/.cache/nycresolver``.
    """
    env_override = os.environ.get("NYCRESOLVER_CACHE_DIR")
    if env_override:
        return Path(env_override).expanduser()
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache).expanduser() / "nycresolver"
    return Path.home() / ".cache" / "nycresolver"


def _cache_path(cache_dir: Path, dataset_id: str) -> Path:
    return cache_dir / f"{dataset_id}.json"


def _is_cache_fresh(path: Path, ttl_seconds: int) -> bool:
    if ttl_seconds <= 0:
        return False
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    fetched_at = payload.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return False
    return (time.time() - fetched_at) < ttl_seconds


def _read_cache(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    if not isinstance(records, list):
        raise ValueError(f"Cache file {path} is corrupted: 'records' is not a list")
    return records


def _write_cache(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": time.time(),
        "records": rows,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _build_url(
    domain: str,
    dataset_id: str,
    limit: int,
    offset: int,
    app_token: Optional[str],
) -> str:
    query = {
        "$limit": str(limit),
        "$offset": str(offset),
        "$order": "record_id",
    }
    if app_token:
        query["$$app_token"] = app_token
    return f"https://{domain}/resource/{dataset_id}.json?{urllib.parse.urlencode(query)}"


def _fetch_page(url: str, user_agent: str, timeout: float) -> list[dict[str, Any]]:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise SocrataError(
            f"Socrata returned HTTP {exc.code} for {url}: {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise SocrataError(f"Network error fetching {url}: {exc.reason}") from exc
    data = json.loads(body)
    if not isinstance(data, list):
        raise SocrataError(f"Unexpected Socrata response shape from {url}")
    return data


def fetch_all_rows(
    dataset_id: str = DEFAULT_DATASET_ID,
    domain: str = DEFAULT_DOMAIN,
    page_size: int = DEFAULT_PAGE_SIZE,
    app_token: Optional[str] = None,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """Fetch every row from the dataset by paging through Socrata."""
    if app_token is None:
        app_token = os.environ.get("SOCRATA_APP_TOKEN") or None

    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        url = _build_url(domain, dataset_id, page_size, offset, app_token)
        page = _fetch_page(url, user_agent, timeout)
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def load_canonical(
    *,
    dataset_id: str = DEFAULT_DATASET_ID,
    domain: str = DEFAULT_DOMAIN,
    cache_dir: Optional[Path] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    refresh: bool = False,
    app_token: Optional[str] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout: float = 30.0,
) -> list[CanonicalRecord]:
    """Load canonical records, hitting the cache when it's fresh enough.

    Parameters
    ----------
    refresh:
        Force a re-fetch even if the cache is within the TTL.
    ttl_seconds:
        Cache lifetime in seconds. Set to ``0`` to disable caching entirely.
    cache_dir:
        Override the default cache directory (``~/.cache/nycresolver``).
    """
    directory = cache_dir or default_cache_dir()
    path = _cache_path(directory, dataset_id)

    if not refresh and _is_cache_fresh(path, ttl_seconds):
        rows = _read_cache(path)
    else:
        rows = fetch_all_rows(
            dataset_id=dataset_id,
            domain=domain,
            page_size=page_size,
            app_token=app_token,
            timeout=timeout,
        )
        if ttl_seconds > 0:
            _write_cache(path, rows)

    return [CanonicalRecord.from_row(row) for row in rows]


def load_canonical_from_file(path: Path) -> list[CanonicalRecord]:
    """Load canonical records from a local JSON file.

    The file may be either a bare list of rows (the raw Socrata response)
    or a cache file wrapping the list under a ``records`` key. Useful for
    tests, offline use, and CI pipelines that don't have network access.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "records" in payload:
        rows = payload["records"]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError(f"Cannot parse canonical data from {path}")
    return [CanonicalRecord.from_row(row) for row in rows]


class SocrataError(RuntimeError):
    """Raised when the Socrata API returns an unexpected status or payload."""
