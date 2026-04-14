# nyc-entity-resolver

Reconcile NYC agency names, acronyms, and organizational identifiers against the
canonical [NYC Governance Organizations dataset][dataset]. A project of NYC's
[Office of Data Analytics (ODA)][oda].

[dataset]: https://data.cityofnewyork.us/City-Government/NYC-Governance-Organizations/t3jq-9nkf
[oda]: https://www.nyc.gov/site/oda/index.page

## Why this exists

NYC open datasets reference agencies by a huge variety of names: the canonical
name, an alphabetized variant, an acronym, a budget code, a former name from
before a reorganization, or (most often) whatever the analyst typing the row
happened to remember. Joining data across datasets means reconciling these
identifiers by hand, and that reconciliation is slow, error-prone, and
repetitive.

`nycresolver` automates it. Give it a column of messy agency labels, and it
fuzzy-matches each value against the current canonical dataset — returning a
crosswalk CSV you can join against, plus a confidence score and tier so you
know which matches to trust and which to review.

## What it matches

The matcher walks each canonical record's full set of identity variants —
preferred name, alphabetized form, alternate / former names, and acronyms
(both current and historical) — and scores the input against each with a
layered strategy:

| Signal | Score | Catches |
| --- | --- | --- |
| **Exact name match** (case-insensitive, whitespace/punctuation-normalized) | 100 | `Department of Finance`, `department of finance`, `Dept. of Finance,` |
| **Exact acronym match** against `acronym` or `alternate_or_former_acronyms` | 100 | `DOF`, `dof`, `DSNY`, `DOS` |
| **Abbreviation expansion** (expand shorthand, then token-sort equal) | 97 | `Dept of Finance`, `Finance, Dept of`, `Fire Dept` |
| **Generated acronym** (input equals first-letters-of-significant-words) | 92 | `MOPI` → `Mayor's Office of Pensions and Investments` when no `acronym` field is set |
| **Fuzzy composite** (weighted combo of Levenshtein, token-sort, Jaccard, etc.) | 0–100 | Typos (`Deparment of Finance`), reordered words, partial overlaps |

The fuzzy composite is a weighted sum of:

- **Levenshtein ratio** — character-level edit distance, catches typos.
- **Token-sort ratio** — Levenshtein after alphabetically sorting tokens;
  catches reordered variants.
- **Token-sort *expanded* ratio** — the same, but after expanding abbreviations.
  The strongest signal for most mixed-case fuzzy matches.
- **Abbreviation-expanded ratio** — Levenshtein after expanding known NYC
  government abbreviations (`dept → department`, `admin → administration`,
  `nyc → new york city`, ...).
- **Jaccard of meaningful tokens** — set overlap of expanded tokens after
  dropping function words (`of`, `and`, `the`, ...).
- **Contains bonus** — partial credit when one normalized form is a substring
  of the other.

## Confidence tiers

| Tier | Score range | How to treat it |
| --- | --- | --- |
| **Exact** | 100 | Trust automatically. |
| **High** | 85–99 | Trust in bulk workflows; spot-check a sample if stakes are high. |
| **Medium** | 65–84 | Needs human review — usually right, but worth a glance. |
| **Low** | 45–64 | Surfaced as a candidate; don't trust without reviewing. |
| **No match** | <45 | Unmatched. Consider adding an alias or abbreviation (see below). |

The CLI and web UI both flag everything below the **high** tier as
`needs_review = true` so you can filter.

## Installation

```bash
pip install -e .
# or, for tests:
pip install -e ".[dev]"
```

Requires Python 3.9 or later. The package has zero runtime dependencies —
everything uses the standard library.

## CLI usage

```bash
nycresolver INPUT.csv --column AGENCY_NAME > crosswalk.csv
```

Common options:

```text
-c, --column COLUMN            Column in the input file to match (required)
-o, --output PATH              Write crosswalk to PATH (default: stdout)
-t, --threshold SCORE          Drop output rows below this confidence score
    --min-score SCORE          Treat matches below this as no-match (default: 45)
    --format {csv,tsv}         Output format (default: csv)
    --input-format {auto,csv,tsv}
                               Input format (default: auto-detect)
    --canonical-file PATH      Use a local JSON file instead of Socrata (offline)
    --refresh                  Force a refetch of the canonical dataset
    --no-cache                 Disable local caching entirely
    --ttl SECONDS              Cache TTL (default: 86400, i.e. 24h)
    --cache-dir PATH           Override local cache directory
    --dataset-id ID            Override the Socrata dataset ID
    --quiet                    Suppress end-of-run summary on stderr
```

The crosswalk CSV has these columns:

```text
input_value, matched_canonical_name, matched_acronym, matched_record_id,
matched_variant, confidence_score, confidence_tier, match_type, needs_review
```

### Example

Given this input (from [`examples/sample_input.csv`](examples/sample_input.csv)):

```csv
row_id,agency_name,fiscal_year,notes
1,DOF,2024,exact acronym
2,Dept of Finance,2024,abbreviation
3,"Finance, Dept of",2024,word reordering
4,Deparment of Finance,2024,one-letter typo
5,NYC Sanitation,2024,alternate name
6,DSNY,2024,exact acronym
7,Fire Dept,2024,abbreviation
8,Schrödinger's Department,2024,no match
```

Running:

```bash
nycresolver examples/sample_input.csv --column agency_name
```

Produces rows like:

```csv
input_value,matched_canonical_name,matched_acronym,...,confidence_score,confidence_tier,match_type
DOF,Department of Finance,DOF,...,100.00,exact,exact_acronym
Dept of Finance,Department of Finance,DOF,...,97.00,high,abbreviation_expansion
"Finance, Dept of",Department of Finance,DOF,...,97.00,high,abbreviation_expansion
Departon of Finance,Department of Finance,DOF,...,79.42,medium,fuzzy
NYC Sanitation,Department of Sanitation,DSNY,...,100.00,exact,exact_alternate_name
DSNY,Department of Sanitation,DSNY,...,100.00,exact,exact_acronym
Fire Dept,Fire Department,FDNY,...,97.00,high,abbreviation_expansion
Schrödinger's Department,,,...,0.00,none,no_match
```

See [`examples/sample_crosswalk.csv`](examples/sample_crosswalk.csv) for the
full expected output.

### Programmatic use

```python
from nycresolver import build_matcher

matcher = build_matcher()
result = matcher.match("Dept. of Finance")

print(result.matched_canonical_name)   # Department of Finance
print(result.matched_acronym)          # DOF
print(result.confidence_score)         # 97.0
print(result.confidence_tier)          # high
print(result.match_type)               # abbreviation_expansion
```

For batch work:

```python
results = matcher.batch(["DOF", "Dept. of Finance", "NYC Sanitation", "XYZ"])
```

## Web UI

The web UI in [`web/index.html`](web/index.html) is a single-file app that
runs everything client-side: it fetches the canonical dataset from Socrata,
runs the same matching pipeline in JavaScript, and exports a crosswalk CSV
from your browser. **No uploaded data ever leaves the browser.**

To use it locally, serve the `web/` directory over HTTP (fetching from
Socrata requires an HTTP origin rather than `file://`):

```bash
python -m http.server --directory web 8080
# open http://localhost:8080
```

## Caching

The CLI caches the Socrata response in `~/.cache/nycresolver/` (or
`$XDG_CACHE_HOME/nycresolver/`, or `$NYCRESOLVER_CACHE_DIR`) for 24 hours by
default. To refresh earlier:

```bash
nycresolver input.csv --column col --refresh
```

## Contributing

The quickest improvement is usually **adding an abbreviation**. If you see the
matcher miss a common shorthand — say, `Commr` for `Commissioner`, or some
agency-specific slang — add it to [`src/nycresolver/abbreviations.py`][abbr]
and open a PR. Include at least one test case in `tests/test_matcher.py`
demonstrating the fix.

[abbr]: src/nycresolver/abbreviations.py

Found a persistent alias that should live in the canonical data itself (e.g. a
former name, a widely-used nickname)? Open an issue or PR against the
[`MODA-NYC/nyc-governance-organizations`][source] source repo so every
downstream consumer benefits.

[source]: https://github.com/MODA-NYC/nyc-governance-organizations

### Running the tests

```bash
pip install -e ".[dev]"
pytest
```

## Design notes & future work

- **Live data, not a snapshot.** The matcher always fetches the canonical
  dataset from Socrata rather than bundling a static copy, so it picks up
  corrections and additions automatically.
- **Python stdlib only.** No `requests`, no `fuzzywuzzy`, no `pandas`. The
  package is ~1000 lines and installs in a second.
- **Extensibility points already in place:**
  - `Matcher(weights=...)` lets you tune the composite weights.
  - `Matcher(min_score=...)` adjusts the no-match threshold.
  - `load_canonical_from_file()` lets you substitute a local JSON file for
    tests or offline runs.
- **Planned but not yet implemented:**
  - Budget-code matching once a `budget_code` field lands on the canonical
    dataset.
  - Organization-hierarchy fallback: if a sub-unit doesn't match, walk up
    to the parent agency.
  - Batch HTTP API for programmatic integration.
  - Community-maintained alias table that accumulates analyst corrections
    over time and feeds them back upstream.

## Related

- **Canonical dataset** — [NYC Governance Organizations (t3jq-9nkf)][dataset]
- **Source repo for the canonical dataset** —
  [MODA-NYC/nyc-governance-organizations][source]

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
