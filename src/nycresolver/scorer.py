"""Pure scoring functions for fuzzy matching NYC agency identifiers.

All functions take two raw strings and return a similarity score in the
closed interval ``[0.0, 1.0]``. They are deterministic and have no external
dependencies beyond :mod:`nycresolver.abbreviations`.
"""

from __future__ import annotations

import re
from typing import FrozenSet

from nycresolver.abbreviations import expand_abbreviations, rewrite_symbols

# Small function words that typically aren't represented in an acronym.
# When generating an acronym from a full agency name we skip these.
STOPWORDS: FrozenSet[str] = frozenset(
    {
        "of",
        "and",
        "the",
        "for",
        "a",
        "an",
        "in",
        "on",
        "to",
        "at",
        "or",
        "by",
        "with",
        "into",
        "from",
    }
)

_WHITESPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not text:
        return ""
    cleaned = rewrite_symbols(text)
    cleaned = cleaned.lower()
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def normalize_expanded(text: str) -> str:
    """Normalize *and* expand known abbreviations in ``text``."""
    if not text:
        return ""
    expanded = expand_abbreviations(text)
    return normalize(expanded)


def tokens(text: str) -> list[str]:
    """Return the whitespace-separated tokens of a normalized string."""
    normalized = normalize(text)
    if not normalized:
        return []
    return normalized.split()


def exact(a: str, b: str) -> float:
    """Return 1.0 if two strings are normalized-equal, 0.0 otherwise."""
    return 1.0 if normalize(a) == normalize(b) and normalize(a) != "" else 0.0


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Iterative Levenshtein edit distance using two rows of memory."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)
    if len(s1) > len(s2):
        s1, s2 = s2, s1
    previous_row = list(range(len(s1) + 1))
    for i, c2 in enumerate(s2, start=1):
        current_row = [i]
        for j, c1 in enumerate(s1, start=1):
            insertions = previous_row[j] + 1
            deletions = current_row[j - 1] + 1
            substitutions = previous_row[j - 1] + (0 if c1 == c2 else 1)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def _ratio_from_distance(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    distance = _levenshtein_distance(a, b)
    max_len = max(len(a), len(b))
    return 1.0 - (distance / max_len)


def levenshtein_ratio(a: str, b: str) -> float:
    """Normalized Levenshtein similarity (1 - edit_distance / max_len)."""
    return _ratio_from_distance(normalize(a), normalize(b))


def abbreviation_expanded_ratio(a: str, b: str) -> float:
    """Levenshtein similarity after expanding abbreviations in both strings.

    Catches matches like ``Dept of Finance`` vs ``Department of Finance``
    that would otherwise score moderately on raw Levenshtein alone.
    """
    return _ratio_from_distance(normalize_expanded(a), normalize_expanded(b))


def token_sort_ratio(a: str, b: str) -> float:
    """Levenshtein similarity after alphabetically sorting each string's tokens.

    Catches word-reordered variants like ``Finance, Department of`` vs
    ``Department of Finance``.
    """
    ta = " ".join(sorted(tokens(a)))
    tb = " ".join(sorted(tokens(b)))
    return _ratio_from_distance(ta, tb)


def _tokens_expanded(text: str) -> list[str]:
    normalized = normalize_expanded(text)
    if not normalized:
        return []
    return normalized.split()


def token_sort_expanded_ratio(a: str, b: str) -> float:
    """Token-sort ratio after expanding abbreviations in both strings.

    The strongest signal for reordered *and* abbreviated variants — e.g.
    ``Finance, Dept of`` → ``department finance of`` matches
    ``Department of Finance`` → ``department finance of``.
    """
    ta = " ".join(sorted(_tokens_expanded(a)))
    tb = " ".join(sorted(_tokens_expanded(b)))
    return _ratio_from_distance(ta, tb)


def sorted_normalized_expanded(text: str) -> str:
    """Return the alphabetically-sorted space-joined tokens of the expanded form.

    Two strings with identical output here are the same agency name up to
    word order, punctuation, case, and known abbreviations.
    """
    return " ".join(sorted(_tokens_expanded(text)))


def sorted_meaningful_expanded(text: str) -> str:
    """Like :func:`sorted_normalized_expanded` but with stopwords removed.

    Two strings with identical output here share the same set of meaningful
    (non-function) words, modulo order and abbreviation. Used as a slightly
    looser abbreviation-expansion match: it catches ``Finance Dept`` ↔
    ``Department of Finance`` where the short form lacks the ``of``.
    """
    return " ".join(
        sorted(t for t in _tokens_expanded(text) if t not in STOPWORDS)
    )


def jaccard_tokens(a: str, b: str) -> float:
    """Jaccard similarity of the two strings' token sets (|A ∩ B| / |A ∪ B|)."""
    ta = set(tokens(a))
    tb = set(tokens(b))
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    intersection = ta & tb
    union = ta | tb
    return len(intersection) / len(union)


def jaccard_meaningful(a: str, b: str) -> float:
    """Jaccard over *expanded* token sets with stopwords removed.

    Strips short function words (``of``, ``and``, ``the``, etc.) before the
    comparison so that ``Finance Department`` and ``Department of Finance``
    have identical signal, but ``NYC Sanitation`` and ``NYC Buildings``
    don't get credit for sharing ``nyc``.
    """
    ta = {t for t in _tokens_expanded(a) if t not in STOPWORDS}
    tb = {t for t in _tokens_expanded(b) if t not in STOPWORDS}
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    intersection = ta & tb
    union = ta | tb
    return len(intersection) / len(union)


def generated_acronym(text: str) -> str:
    """Build an acronym from ``text`` by taking the first letter of each
    non-stopword token. Returns a lowercase string."""
    parts: list[str] = []
    for token in normalize(text).split():
        if token in STOPWORDS:
            continue
        parts.append(token[0])
    return "".join(parts)


def acronym_match(input_value: str, canonical_name: str) -> float:
    """Return 1.0 if ``input_value`` could plausibly be an acronym of
    ``canonical_name`` (or vice versa), 0.0 otherwise.

    This is the *generated* acronym signal — matching against a canonical
    ``acronym`` field is handled separately by the matcher as an explicit
    alias lookup.
    """
    compact_input = normalize(input_value).replace(" ", "")
    compact_canonical = normalize(canonical_name).replace(" ", "")
    if not compact_input or not compact_canonical:
        return 0.0
    if compact_input == generated_acronym(canonical_name):
        return 1.0
    if compact_canonical == generated_acronym(input_value):
        return 1.0
    return 0.0


def contains_bonus(a: str, b: str) -> float:
    """Substring containment ratio in [0.0, 1.0].

    Returns ``len(shorter) / len(longer)`` when one normalized string is a
    substring of the other. Lets short inputs like ``Finance`` pick up credit
    for appearing inside ``Department of Finance`` without inflating the
    score of unrelated pairs.
    """
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if shorter in longer:
        return len(shorter) / len(longer)
    return 0.0
