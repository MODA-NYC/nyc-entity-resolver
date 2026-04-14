"""Reconcile NYC agency identifiers against the canonical Governance Organizations dataset."""

from __future__ import annotations

from nycresolver.matcher import Match, MatchResult, Matcher, build_matcher

__all__ = ["Match", "MatchResult", "Matcher", "build_matcher", "__version__"]

__version__ = "0.1.0"
