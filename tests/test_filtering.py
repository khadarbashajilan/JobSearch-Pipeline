"""Tests for the freshness + exclusion + level filter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jobsearch.filtering import filter_jobs, infer_level
from jobsearch.models import StoredJob


def _stored(title: str, **overrides) -> StoredJob:
    base = dict(
        id="x",
        source="indeed",
        source_id=None,
        title=title,
        company="Acme",
        location="Remote",
    )
    base.update(overrides)
    return StoredJob(**base)


def test_filter_drops_jobs_outside_window() -> None:
    old = datetime.now(timezone.utc) - timedelta(hours=200)
    jobs = [_stored("Junior Engineer", date_posted=old)]
    out, stats = filter_jobs(jobs, hours_old=72, exclude_keywords=())
    assert out == []
    assert stats.dropped_outside_window == 1


def test_filter_keeps_undated_jobs() -> None:
    jobs = [_stored("Junior Engineer", date_posted=None)]
    out, stats = filter_jobs(jobs, hours_old=72, exclude_keywords=())
    assert out == jobs
    assert stats.kept == 1


def test_filter_keeps_recent_jobs() -> None:
    fresh = datetime.now(timezone.utc) - timedelta(hours=2)
    jobs = [_stored("Junior Engineer", date_posted=fresh)]
    out, stats = filter_jobs(jobs, hours_old=72, exclude_keywords=())
    assert out == jobs
    assert stats.dropped_outside_window == 0


def test_filter_drops_excluded_titles_whole_word() -> None:
    jobs = [_stored("Senior Engineer")]
    out, stats = filter_jobs(jobs, hours_old=None, exclude_keywords=("Senior",))
    assert out == []
    assert stats.dropped_excluded_title == 1


def test_filter_does_not_drop_substring_matches() -> None:
    """'Java' must not match 'JavaScript' — that was the v1 substring bug."""
    jobs = [_stored("JavaScript Engineer")]
    out, stats = filter_jobs(jobs, hours_old=None, exclude_keywords=("Java",))
    assert out == jobs
    assert stats.dropped_excluded_title == 0


def test_filter_handles_naive_datetime() -> None:
    """Naive timestamps from older sources should still work."""
    naive = (datetime.now() - timedelta(hours=1)).replace(tzinfo=None)
    jobs = [_stored("Junior Engineer", date_posted=naive)]
    out, _ = filter_jobs(jobs, hours_old=72, exclude_keywords=())
    assert out == jobs


def test_filter_does_not_crash_on_invalid_timestamp(tmp_path) -> None:
    jobs = [_stored("Junior Engineer", date_posted="not-a-date")]  # type: ignore[arg-type]
    # The type is intentionally wrong — filtering must not crash.
    out, stats = filter_jobs(jobs, hours_old=72, exclude_keywords=())
    assert out == jobs  # invalid dates are treated as unknown, kept


def test_infer_level_recognises_common_tiers() -> None:
    assert infer_level("Senior Engineer") == "senior"
    assert infer_level("Lead Engineer") == "lead"
    assert infer_level("Staff Engineer") == "staff"
    assert infer_level("Principal Engineer") == "principal"
    assert infer_level("Junior Engineer") == "junior"
    assert infer_level("Software Engineer Intern") == "intern"
    assert infer_level("Entry Level Developer") == "junior"
    assert infer_level("Director of Engineering") == "senior"
    assert infer_level("Head of Product") == "senior"
    assert infer_level("Software Engineer") is None


def test_infer_level_takes_highest_tier_in_composite_titles() -> None:
    assert infer_level("Senior Staff Engineer") == "staff"
    assert infer_level("Lead Senior Engineer") == "lead"


def test_level_filter_off_is_a_no_op() -> None:
    jobs = [_stored("Senior Engineer"), _stored("Principal Architect")]
    out, stats = filter_jobs(jobs, hours_old=None, exclude_keywords=(), level_filter="off")
    assert out == jobs
    assert stats.dropped_level_above == 0


def test_level_filter_junior_drops_senior_and_above() -> None:
    jobs = [
        _stored("Junior Engineer"),
        _stored("Software Engineer"),
        _stored("Senior Engineer"),
        _stored("Lead Engineer"),
        _stored("Staff Engineer"),
        _stored("Principal Architect"),
        _stored("Director of Engineering"),
        _stored("Entry Level Developer"),
    ]
    out, stats = filter_jobs(
        jobs, hours_old=None, exclude_keywords=(), level_filter="junior"
    )
    kept_titles = [job.title for job in out]
    assert "Junior Engineer" in kept_titles
    assert "Entry Level Developer" in kept_titles
    assert "Software Engineer" in kept_titles  # unknown seniority passes through
    assert "Senior Engineer" not in kept_titles
    assert "Lead Engineer" not in kept_titles
    assert "Staff Engineer" not in kept_titles
    assert "Principal Architect" not in kept_titles
    assert "Director of Engineering" not in kept_titles
    assert stats.dropped_level_above == 5


def test_level_filter_senior_keeps_mid_and_drops_staff() -> None:
    jobs = [
        _stored("Mid-level Engineer"),
        _stored("Senior Engineer"),
        _stored("Staff Engineer"),
        _stored("Principal Architect"),
    ]
    out, stats = filter_jobs(
        jobs, hours_old=None, exclude_keywords=(), level_filter="senior"
    )
    kept_titles = [job.title for job in out]
    assert "Mid-level Engineer" in kept_titles
    assert "Senior Engineer" in kept_titles
    assert "Staff Engineer" not in kept_titles
    assert "Principal Architect" not in kept_titles
    assert stats.dropped_level_above == 2


def test_level_filter_unknown_value_passes_through() -> None:
    # Defensive: even if an unknown value sneaks through, no jobs are
    # dropped (the matcher treats ``off`` as the safe default).
    jobs = [_stored("Senior Engineer")]
    out, stats = filter_jobs(jobs, hours_old=None, exclude_keywords=(), level_filter="bogus")
    assert out == jobs
    assert stats.dropped_level_above == 0