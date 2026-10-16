"""Tests for SQLite persistence and application status updates."""

from __future__ import annotations

from datetime import datetime

import pytest

from jobsearch.errors import PersistenceError
from jobsearch.ids import job_id
from jobsearch.models import (
    ApplicationStatus,
    EmploymentType,
    LinkStatus,
    StoredJob,
    WorkArrangement,
)
from jobsearch import store


def _job(**overrides) -> StoredJob:
    base = dict(
        id="abcd" * 10,
        source="indeed",
        source_id="1",
        title="Engineer",
        company="Acme",
        location="Remote",
        work_arrangement=WorkArrangement.UNKNOWN,
        employment_type=EmploymentType.FULL_TIME,
        source_url="https://example.com/jobs/1",
        link_status=LinkStatus.UNVERIFIED,
        application_status=ApplicationStatus.NEW,
    )
    base.update(overrides)
    return StoredJob(**base)


def test_upsert_inserts_and_updates(temp_db) -> None:
    store.upsert_jobs([_job()], path=temp_db)
    rows = store.list_jobs(path=temp_db)
    assert len(rows) == 1
    assert rows[0].title == "Engineer"


def test_upsert_merges_on_second_run(temp_db) -> None:
    job = _job()
    store.upsert_jobs([job], path=temp_db)
    refreshed = _job(id=job.id, title="Senior Engineer", source_id="1", source_url=job.source_url)
    store.upsert_jobs([refreshed], path=temp_db)
    rows = store.list_jobs(path=temp_db)
    assert len(rows) == 1
    assert rows[0].title == "Senior Engineer"


def test_update_status_records_history(temp_db) -> None:
    job = _job()
    store.upsert_jobs([job], path=temp_db)
    store.update_status(job.id, ApplicationStatus.APPLIED, note="via email", path=temp_db)
    rows = store.list_jobs(path=temp_db)
    assert rows[0].application_status == ApplicationStatus.APPLIED


def test_update_status_unknown_id_raises(temp_db) -> None:
    with pytest.raises(PersistenceError, match="unknown job id"):
        store.update_status("nope", ApplicationStatus.APPLIED, path=temp_db)


def test_list_filters_by_status(temp_db) -> None:
    store.upsert_jobs([_job(id="a" * 40), _job(id="b" * 40)], path=temp_db)
    store.update_status("a" * 40, ApplicationStatus.APPLIED, path=temp_db)
    applied = store.list_jobs(statuses=(ApplicationStatus.APPLIED,), path=temp_db)
    assert len(applied) == 1
    assert applied[0].application_status == ApplicationStatus.APPLIED


def test_run_lifecycle_is_recorded(temp_db) -> None:
    run_id = store.start_run(location="Remote", query="x", diagnostics={"indeed": {"found": 1}}, path=temp_db)
    store.finish_run(run_id, path=temp_db)
    with store.connect(temp_db) as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        assert row["finished_at"] is not None