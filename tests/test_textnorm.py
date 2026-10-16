"""Tests for parsing and normalisation helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from jobsearch.textnorm import contains_any, normalize_title, parse_iso_datetime


def test_parse_iso_datetime_handles_z_suffix() -> None:
    dt = parse_iso_datetime("2024-09-01T12:00:00Z")
    assert dt == datetime(2024, 9, 1, 12, 0, tzinfo=timezone.utc)


def test_parse_iso_datetime_assumes_utc_for_naive() -> None:
    dt = parse_iso_datetime("2024-09-01T12:00:00")
    assert dt.tzinfo is not None


def test_parse_iso_datetime_returns_none_for_garbage() -> None:
    assert parse_iso_datetime("not-a-date") is None
    assert parse_iso_datetime(None) is None
    assert parse_iso_datetime("") is None


def test_normalize_title_strips_punctuation() -> None:
    assert normalize_title("Senior, AI Engineer!") == "senior ai engineer"


def test_contains_any_matches_whole_tokens() -> None:
    needles = ("java", "python")
    assert contains_any("Looking for python devs", needles) == ("python",)
    assert contains_any("JavaScript wizard", needles) == ()  # not "java"


def test_contains_any_case_insensitive() -> None:
    assert contains_any("Python is great", ("PYTHON",)) == ("python",)