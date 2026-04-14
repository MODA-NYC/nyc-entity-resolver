"""NYC government abbreviation dictionary and expansion helpers.

The abbreviations here are curated from common shorthand that appears in NYC
open-data columns, procurement records, and internal spreadsheets when
analysts type agency names by hand. Expansion works at the whole-word level
(case-insensitive), so ``Dept of Finance`` becomes ``department of finance``
without matching unrelated substrings like ``develop``.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

# Map of abbreviation → canonical expansion. Keys are lowercase, unpunctuated.
# When you add an entry, add its most common punctuated/plural forms too if
# they are likely to appear verbatim in user data.
ABBREVIATIONS: Mapping[str, str] = {
    # Organizational units
    "dept": "department",
    "depts": "departments",
    "div": "division",
    "divs": "divisions",
    "ofc": "office",
    "ofcs": "offices",
    "off": "office",
    "admin": "administration",
    "admn": "administration",
    "auth": "authority",
    "bd": "board",
    "brd": "board",
    "bldg": "building",
    "bur": "bureau",
    "comm": "commission",
    "commn": "commission",
    "cmsn": "commission",
    "cncl": "council",
    "cnsl": "council",
    "corp": "corporation",
    "ctte": "committee",
    "cmte": "committee",
    "cmt": "committee",
    "ctr": "center",
    "cntr": "center",
    "fdn": "foundation",
    "inst": "institute",
    # Functional / topical
    "acad": "academy",
    "agr": "agriculture",
    "comms": "communications",
    "comp": "comptroller",
    "dev": "development",
    "devt": "development",
    "ed": "education",
    "educ": "education",
    "emerg": "emergency",
    "env": "environmental",
    "envir": "environmental",
    "envt": "environment",
    "fin": "finance",
    "gen": "general",
    "govt": "government",
    "gov": "government",
    "hlth": "health",
    "hsg": "housing",
    "hr": "human resources",
    "info": "information",
    "intl": "international",
    "invest": "investigation",
    "mgmt": "management",
    "mgt": "management",
    "mkts": "markets",
    "oper": "operations",
    "ops": "operations",
    "plng": "planning",
    "pres": "preservation",
    "prot": "protection",
    "pub": "public",
    "rec": "recreation",
    "res": "resources",
    "svc": "service",
    "svcs": "services",
    "serv": "service",
    "servs": "services",
    "tech": "technology",
    "trans": "transportation",
    "transp": "transportation",
    "trnsp": "transportation",
    "util": "utility",
    # People
    "mgr": "manager",
    "ofcr": "officer",
    # Place / jurisdiction
    "bklyn": "brooklyn",
    "bx": "bronx",
    "mnhtn": "manhattan",
    "qns": "queens",
    "si": "staten island",
    "nyc": "new york city",
    "ny": "new york",
    "us": "united states",
    "usa": "united states",
}

# Bidirectional aliases that are *not* abbreviations but rewrite common
# punctuation / conjunctions to a canonical form before scoring.
SYMBOL_REWRITES: Mapping[str, str] = {
    "&": "and",
    "@": "at",
    "/": " ",
    "-": " ",
    "_": " ",
    ".": " ",
    ",": " ",
    ";": " ",
    ":": " ",
    "(": " ",
    ")": " ",
    "'": "",
    "\"": "",
}

_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def expand_abbreviations(text: str, extra: Mapping[str, str] | None = None) -> str:
    """Return ``text`` with known abbreviations replaced by full forms.

    Operates at the whole-word level and is case-insensitive. Non-abbreviated
    words are left untouched. Punctuation is preserved (the scorer's
    ``normalize`` strips it later).
    """
    lookup: dict[str, str] = dict(ABBREVIATIONS)
    if extra:
        lookup.update({k.lower(): v for k, v in extra.items()})

    def _replace(match: re.Match[str]) -> str:
        word = match.group(0)
        expansion = lookup.get(word.lower())
        if expansion is None:
            return word
        return expansion

    return _WORD_RE.sub(_replace, text)


def rewrite_symbols(text: str) -> str:
    """Normalize common punctuation / symbols to spaces or canonical words."""
    result = text
    for symbol, replacement in SYMBOL_REWRITES.items():
        if symbol in result:
            result = result.replace(symbol, replacement)
    return result


def contains_abbreviation(text: str) -> bool:
    """Return True if ``text`` contains at least one known abbreviation token."""
    for match in _WORD_RE.finditer(text):
        if match.group(0).lower() in ABBREVIATIONS:
            return True
    return False


def known_abbreviations() -> Iterable[str]:
    """Iterate over the abbreviation keys. Useful for tests and docs."""
    return ABBREVIATIONS.keys()
