"""Tests for deterministic matching."""

from __future__ import annotations

import pytest

from jobsearch.matching import match_job, rank_matches
from jobsearch.models import (
    EmploymentType,
    LinkStatus,
    StoredJob,
    WorkArrangement,
)
from jobsearch.profile import CandidateProfile


def _profile(**overrides) -> CandidateProfile:
    base = dict(
        primary_title="GenAI Developer",
        alternative_titles=("AI Engineer",),
        skills=("Python", "FastAPI", "LangChain"),
        exclude_keywords=(),
        location="Bengaluru, India",
        country="India",
        jobspy_country="india",
        remote_preference="any",
        employment_types=("full_time",),
        experience_years=4,
        min_score=0.0,
    )
    base.update(overrides)
    return CandidateProfile(**base)


def _job(title="GenAI Developer", company="Acme", **overrides) -> StoredJob:
    base = dict(
        id="x",
        source="indeed",
        source_id="1",
        title=title,
        company=company,
        location="Bengaluru, India",
        work_arrangement=WorkArrangement.UNKNOWN,
        employment_type=EmploymentType.FULL_TIME,
        description="",
        required_skills=("Python", "FastAPI"),
        source_url="https://example.com/jobs/1",
        link_status=LinkStatus.UNVERIFIED,
    )
    base.update(overrides)
    return StoredJob(**base)


def test_strong_match_scores_high() -> None:
    score = match_job(_job(), _profile()).score
    assert score > 0.7


def test_missing_required_skills_lowers_score() -> None:
    strong = match_job(_job(required_skills=("Python", "FastAPI")), _profile()).score
    weak = match_job(_job(required_skills=("Rust", "Haskell")), _profile()).score
    assert strong > weak


def test_unknown_skills_does_not_penalise() -> None:
    profile = _profile(experience_years=None)
    score = match_job(_job(required_skills=()), profile).score
    # Should be neutral on the skill component, not penalised.
    assert score > 0


def test_remote_preference_onsite_conflict_is_hard() -> None:
    profile = _profile(remote_preference="remote")
    job = _job(work_arrangement=WorkArrangement.ONSITE)
    match = match_job(job, profile)
    assert match.score == 0.0
    assert match.hard_conflicts


def test_alternative_title_contributes_to_alignment() -> None:
    profile = _profile()
    job_with_primary = _job(title="GenAI Developer")
    job_with_alt = _job(title="AI Engineer")
    score_primary = match_job(job_with_primary, profile).score
    score_alt = match_job(job_with_alt, profile).score
    assert score_alt > 0.3  # alt title still scores well, not zero


def test_seniority_required_can_demote() -> None:
    profile = _profile(experience_years=1)
    job = _job(title="Principal Engineer")
    score = match_job(job, profile).score
    assert score < 0.7


def test_rank_orders_by_score() -> None:
    profile = _profile()
    good = _job(id="a", title="GenAI Developer", source_url="https://x/a")
    bad = _job(id="b", title="Sales Manager", source_url="https://x/b")
    ranked = rank_matches([bad, good], profile)
    assert ranked[0].job.id == "a"
    assert ranked[0].score >= ranked[-1].score


def test_unknown_employment_type_is_neutral() -> None:
    profile = _profile(employment_types=("full_time",))
    job = _job(employment_type=EmploymentType.UNKNOWN)
    match = match_job(job, profile)
    # Not 0 — unknown is neutral, not negative.
    assert match.score > 0.2


def test_match_returns_explanation_text() -> None:
    match = match_job(_job(), _profile())
    assert match.explanation
    assert "matched" in match.explanation or "no signals" in match.explanation