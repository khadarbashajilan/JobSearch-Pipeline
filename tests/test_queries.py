"""Tests for query derivation."""

from __future__ import annotations

from jobsearch.profile import CandidateProfile
from jobsearch.queries import build_queries, parse_sites


def _profile(**overrides) -> CandidateProfile:
    base = dict(
        primary_title="GenAI Developer",
        alternative_titles=("Generative AI Engineer", "AI Engineer"),
        skills=("Python", "FastAPI", "LangChain"),
        exclude_keywords=("Senior", "Lead"),
        location="Bengaluru, India",
    )
    base.update(overrides)
    return CandidateProfile(**base)


def test_build_queries_boolean_uses_quoted_title_and_or() -> None:
    queries = build_queries(_profile())
    assert queries["indeed"].startswith('"GenAI Developer" (')
    assert "Python OR FastAPI" in queries["indeed"]
    assert "-Senior" in queries["indeed"]
    assert "-Lead" in queries["indeed"]


def test_build_queries_plain_includes_alternative_titles() -> None:
    queries = build_queries(_profile())
    assert "GenAI Developer" in queries["plain"]
    assert "Python" in queries["plain"]
    assert "Generative AI Engineer" in queries["plain"]


def test_build_queries_profile_override_wins() -> None:
    profile = _profile()
    profile.raw_queries = {"indeed": "custom query"}
    queries = build_queries(profile)
    assert queries["indeed"] == "custom query"


def test_parse_sites_handles_comma_separated() -> None:
    assert parse_sites("linkedin,indeed") == ["linkedin", "indeed"]


def test_parse_sites_rejects_unknown() -> None:
    with __import__("pytest").raises(ValueError, match="unknown site"):
        parse_sites("linkedin,not_a_site")