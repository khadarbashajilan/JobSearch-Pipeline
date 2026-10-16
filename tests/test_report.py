"""Tests for the Markdown report renderer."""

from __future__ import annotations

from datetime import datetime, timezone

from jobsearch.filtering import FilterStats
from jobsearch.models import (
    EmploymentType,
    JobMatch,
    LinkStatus,
    StoredJob,
    WorkArrangement,
)
from jobsearch.orchestrate import RunReport
from jobsearch.profile import CandidateProfile
from jobsearch.report import render


def _profile() -> CandidateProfile:
    return CandidateProfile(
        primary_title="GenAI Developer",
        alternative_titles=("AI Engineer",),
        skills=("Python", "FastAPI"),
        location="Bengaluru, India",
        country="India",
        jobspy_country="india",
        hours_old=72,
        results_per_site=30,
        remote_preference="any",
        employment_types=("full_time",),
    )


def _job(title: str = "GenAI Developer", url: str = "https://example.com/x") -> StoredJob:
    return StoredJob(
        id="x",
        source="indeed",
        title=title,
        company="Acme",
        location="Bengaluru",
        work_arrangement=WorkArrangement.UNKNOWN,
        employment_type=EmploymentType.FULL_TIME,
        source_url=url,
        link_status=LinkStatus.UNVERIFIED,
        date_posted=datetime(2024, 9, 1, tzinfo=timezone.utc),
    )


def test_report_renders_title_and_url() -> None:
    job = _job()
    match = JobMatch(job=job, score=0.8, matched_skills=("python",), missing_skills=(), hard_conflicts=(), explanation="matched")
    report = RunReport(queries={"indeed": "x", "plain": "x"}, discovered=1, deduplicated=1, filtered_out=0, matched=1)
    output = render([match], _profile(), report, FilterStats(kept=1))
    assert "GenAI Developer" in output
    assert "https://example.com/x" in output
    assert "**Score:**" in output
    assert "- Google search: [Acme GenAI Developer careers]" in output
    assert '- Google dork: [site:example.com careers "GenAI Developer"]' in output
    assert "https://www.google.com/search?q=" in output


def test_report_marks_unknown_dates() -> None:
    job = _job()
    job.date_posted = None
    match = JobMatch(job=job, score=0.5, matched_skills=(), missing_skills=(), hard_conflicts=(), explanation="no signals")
    output = render([match], _profile(), RunReport(), FilterStats(kept=1))
    assert "posted date unknown" in output


def test_report_never_leaks_nan() -> None:
    job = _job()
    match = JobMatch(job=job, score=0.5, matched_skills=(), missing_skills=(), hard_conflicts=(), explanation="no signals")
    output = render([match], _profile(), RunReport(), FilterStats(kept=1))
    assert "nan" not in output.lower()


def test_report_surfaces_source_diagnostics() -> None:
    from jobsearch.sources.base import SourceDiagnostic

    report = RunReport(
        diagnostics=[SourceDiagnostic(source="indeed", jobs_found=0, error="blocked")]
    )
    output = render([], _profile(), report, FilterStats())
    assert "indeed" in output
    assert "blocked" in output


def test_report_honors_min_score() -> None:
    profile = _profile()
    profile.min_score = 0.9
    job = _job()
    match = JobMatch(job=job, score=0.2, matched_skills=(), missing_skills=(), hard_conflicts=(), explanation="no signals")
    output = render([match], profile, RunReport(), FilterStats(kept=1))
    assert "No matching jobs" in output


def test_report_marks_previously_seen() -> None:
    job = _job()
    match = JobMatch(job=job, score=0.8, matched_skills=(), missing_skills=(), hard_conflicts=(), explanation="")
    output = render([match], _profile(), RunReport(), FilterStats(kept=1), previously_seen_ids={job.id})
    assert "previously seen" in output