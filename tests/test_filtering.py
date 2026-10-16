"""Tests for the freshness + exclusion filter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jobsearch.filtering import filter_jobs
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