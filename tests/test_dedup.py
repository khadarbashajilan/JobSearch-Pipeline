"""Tests for deduplication."""

from __future__ import annotations

from jobsearch.dedup import dedupe, is_same_vacancy, to_stored
from jobsearch.models import EmploymentType, RawJob, WorkArrangement


def _raw(url: str, *, source="indeed", source_id=None, **overrides) -> RawJob:
    base = dict(
        source=source,
        source_id=source_id,
        title="Senior Engineer",
        company="Acme",
        location="Remote",
        work_arrangement=WorkArrangement.UNKNOWN,
        employment_type=EmploymentType.UNKNOWN,
        description="",
        required_skills=(),
        preferred_skills=(),
        source_url=url,
    )
    base.update(overrides)
    return RawJob(**base)


def test_dedupe_collapses_tracking_variants() -> None:
    a = _raw("https://example.com/jobs/123?utm_source=x")
    b = _raw("https://example.com/jobs/123")
    out = dedupe([a, b])
    assert len(out) == 1


def test_dedupe_keeps_distinct_vacancies_same_title() -> None:
    a = _raw("https://example.com/jobs/123", title="Engineer")
    b = _raw("https://example.com/jobs/456", title="Engineer")
    out = dedupe([a, b])
    assert len(out) == 2


def test_dedupe_falls_back_to_ats_id() -> None:
    a = _raw("https://a.example/jobs/1", source="greenhouse", source_id="42")
    b = _raw("https://b.example/jobs/1", source="greenhouse", source_id="42")
    assert is_same_vacancy(a, b) is True
    out = dedupe([a, b])
    assert len(out) == 1


def test_dedupe_does_not_merge_distinct_ats_ids() -> None:
    a = _raw("https://a.example/jobs/1", source="greenhouse", source_id="42")
    b = _raw("https://b.example/jobs/1", source="greenhouse", source_id="99")
    assert is_same_vacancy(a, b) is False


def test_dedupe_handles_missing_source_ids() -> None:
    a = _raw("https://a.example/", source_id=None, title="Engineer", company="Acme")
    b = _raw("https://b.example/", source_id=None, title="Engineer", company="Acme")
    out = dedupe([a, b])
    # No URL and no source id → can't merge safely.
    assert len(out) == 2


def test_to_stored_assigns_stable_id() -> None:
    job = _raw("https://example.com/jobs/123")
    stored = to_stored(job)
    again = to_stored(job)
    assert stored.id == again.id
    assert len(stored.id) == 40  # sha1 hex