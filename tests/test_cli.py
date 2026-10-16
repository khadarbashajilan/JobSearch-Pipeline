"""Tests for the CLI surface."""

from __future__ import annotations

from pathlib import Path

import pytest

from jobsearch.cli import main


def test_inspect_prints_profile(sample_profile_path: Path, capsys) -> None:
    rc = main(["--resume", str(sample_profile_path), "inspect"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "GenAI Developer" in out
    assert "alternative_titles" in out


def test_search_dry_run(sample_profile_path: Path, capsys) -> None:
    rc = main([
        "--resume", str(sample_profile_path),
        "--out-dir", "/tmp/jobspy-test",
        "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "primary title" in out
    assert "derived queries" in out


def test_status_command_unknown_id(sample_profile_path: Path, capsys) -> None:
    rc = main(["--resume", str(sample_profile_path), "status", "fake", "applied"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "unknown job id" in err or "fake" in err


def test_status_command_valid_updates(temp_db, sample_profile_path: Path, capsys) -> None:
    from jobsearch.ids import job_id
    from jobsearch.models import StoredJob, EmploymentType, LinkStatus, WorkArrangement, ApplicationStatus
    from jobsearch import store

    job = StoredJob(
        id=job_id("indeed", "1", "https://example.com/jobs/1"),
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
    store.upsert_jobs([job], path=temp_db)
    rc = main([
        "--resume", str(sample_profile_path),
        "--db", str(temp_db),
        "status", job.id, "applied",
    ])
    assert rc == 0
    rows = store.list_jobs(path=temp_db)
    assert rows[0].application_status == ApplicationStatus.APPLIED


def test_list_command_lists_jobs(temp_db, sample_profile_path: Path, capsys) -> None:
    from jobsearch.models import StoredJob, EmploymentType, LinkStatus, WorkArrangement, ApplicationStatus
    from jobsearch import store

    store.upsert_jobs([
        StoredJob(
            id="abcdef1234567890abcdef1234567890abcdef12",
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
    ], path=temp_db)
    rc = main([
        "--resume", str(sample_profile_path),
        "--db", str(temp_db),
        "list",
    ])
    assert rc == 0
    assert "Engineer" in capsys.readouterr().out