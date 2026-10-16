"""Tests for jobsearch/export.py and the cli hooks that use it.

Covers:
- write_job_markdown: file presence, frontmatter, body, missing-field handling
- status CLI: writes local .md on status change
- status CLI: export failure does not poison the SQLite write
- rclone CLI: errors when nothing to sync, no rclone invoked
- rclone CLI: calls rclone with the right args
- rclone CLI: rclone failure propagates as a non-zero exit
"""

from __future__ import annotations

import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobsearch import export, store
from jobsearch.cli import main
from jobsearch.ids import job_id
from jobsearch.models import (
    ApplicationStatus,
    EmploymentType,
    LinkStatus,
    StoredJob,
    WorkArrangement,
)


# ----- fixtures ---------------------------------------------------------------


@pytest.fixture
def stub_job() -> StoredJob:
    return StoredJob(
        id=job_id("indeed", "1", "https://example.com/jobs/1"),
        source="indeed",
        source_id="1",
        title="Senior Backend Engineer",
        company="Acme Co",
        location="Bengaluru, India",
        work_arrangement=WorkArrangement.REMOTE,
        employment_type=EmploymentType.FULL_TIME,
        description="Build cool things. " * 300,  # ~5700 chars, exceeds the 4000-char truncation limit
        source_url="https://example.com/jobs/1",
        apply_url="https://example.com/jobs/1/apply",
        official_url="https://example.com/jobs/1",
        date_posted=datetime(2026, 10, 16, tzinfo=timezone.utc),
        salary_min=120000.0,
        salary_max=180000.0,
        salary_currency="INR",
        link_status=LinkStatus.VERIFIED,
        application_status=ApplicationStatus.SAVED,
    )


@pytest.fixture
def stored_job(temp_db) -> StoredJob:
    """Insert a job into the temp DB and return it."""
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
    return job


# ----- export.write_job_markdown ---------------------------------------------


def test_export_writes_markdown_file(stub_job, tmp_path: Path) -> None:
    dest = tmp_path / "saved" / "jobs"
    path = export.write_job_markdown(stub_job, dest)

    assert path == dest / f"{stub_job.id}.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")

    # Frontmatter present
    assert text.startswith("---\n")
    assert "id: " + stub_job.id in text
    assert "title: Senior Backend Engineer" in text
    assert "company: Acme Co" in text
    assert "site: indeed" in text
    assert "status: saved" in text

    # Body
    assert "# Senior Backend Engineer - Acme Co" in text
    assert "- **Location:** Bengaluru, India" in text
    assert "- **Site:** indeed" in text
    assert "- **Salary:** INR 120,000-180,000" in text
    assert "- **Link:** https://example.com/jobs/1/apply" in text
    assert "- **Google search:** [Acme Co Senior Backend Engineer careers]" in text
    assert '- **Google dork:** [site:example.com careers "Senior Backend Engineer"]' in text
    assert "https://www.google.com/search?q=" in text
    assert "## Description" in text
    assert "Build cool things." in text

    # Long descriptions are truncated with an ellipsis
    assert text.count("Build cool things.") < 300
    assert text.rstrip().endswith("...")


def test_export_handles_missing_salary_and_url(tmp_path: Path) -> None:
    job = StoredJob(
        id="x" * 40,
        source="linkedin",
        source_id="abc",
        title="Mystery Role",
        company=None,
        location=None,
        source_url="",
        link_status=LinkStatus.UNVERIFIED,
        application_status=ApplicationStatus.NEW,
    )
    path = export.write_job_markdown(job, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "company: " in text
    assert "Mystery Role - (unknown company)" in text
    assert "- **Salary:**" not in text
    assert "- **Link:**" not in text
    assert "_(no description provided)_" in text


def test_export_overwrites_existing_file(stub_job, tmp_path: Path) -> None:
    dest = tmp_path / "saved" / "jobs"
    export.write_job_markdown(stub_job, dest)
    first_mtime = (dest / f"{stub_job.id}.md").stat().st_mtime

    # Mutate the job and re-export; file should be rewritten, not appended
    stub_job.title = "Updated Title"
    path = export.write_job_markdown(stub_job, dest)
    text = path.read_text(encoding="utf-8")
    assert "title: Updated Title" in text
    assert text.count(f"id: {stub_job.id}") == 1  # no double-write


# ----- status CLI hooks -------------------------------------------------------


def test_status_command_writes_local_md(
    stored_job, temp_db, sample_profile_path: Path, tmp_path: Path, monkeypatch
) -> None:
    # Redirect CWD so saved/ lands under tmp_path
    monkeypatch.chdir(tmp_path)
    rc = main([
        "--resume", str(sample_profile_path),
        "--db", str(temp_db),
        "status", stored_job.id, "saved",
    ])
    assert rc == 0
    out = (tmp_path / "saved" / f"{stored_job.id}.md")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "status: saved" in text
    assert "title: Engineer" in text


def test_status_command_warns_on_export_failure_but_exits_zero(
    stored_job, temp_db, sample_profile_path: Path, tmp_path: Path,
    monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    # Make write_job_markdown blow up. The status command must still return 0
    # and the SQLite update must still be visible.
    def boom(*_args, **_kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("jobsearch.cli.export.write_job_markdown", boom)

    rc = main([
        "--resume", str(sample_profile_path),
        "--db", str(temp_db),
        "status", stored_job.id, "applied",
    ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "warning" in err
    assert "disk full" in err

    rows = store.list_jobs(path=temp_db)
    assert rows[0].application_status == ApplicationStatus.APPLIED


# ----- rclone CLI -------------------------------------------------------------


def _make_fake_rclone(tmp_path: Path, *, exit_code: int = 0, record: Path | None = None):
    """Drop a tiny shell script at tmp_path/rclone that exits with ``exit_code``.

    If ``record`` is given, the script appends its argv (one arg per line) to
    that file before exiting, so tests can assert on the exact call.
    """
    script = tmp_path / "rclone"
    record_block = ""
    if record is not None:
        record_block = (
            f"for a in \"$@\"; do echo \"$a\" >> {record}; done\n"
            f"echo '---' >> {record}\n"
        )
    script.write_text(
        f"#!/bin/sh\n{record_block}exit {exit_code}\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def test_rclone_subcommand_errors_when_nothing_to_sync(
    tmp_path: Path, monkeypatch, capsys, sample_profile_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    # saved/ does not exist
    rc = main([
        "--resume", str(sample_profile_path),
        "rclone",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "nothing to sync" in err


def test_rclone_subcommand_errors_when_dir_empty(
    tmp_path: Path, monkeypatch, capsys, sample_profile_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "saved").mkdir(parents=True)
    rc = main([
        "--resume", str(sample_profile_path),
        "rclone",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "nothing to sync" in err


def test_rclone_subcommand_calls_rclone_with_expected_args(
    tmp_path: Path, monkeypatch, sample_profile_path: Path, stub_job
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    record = fake_bin / "calls.log"
    _make_fake_rclone(fake_bin, exit_code=0, record=record)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("JOBSEARCH_RCLONE_REMOTE", "gdrive")
    monkeypatch.setenv("JOBSEARCH_RCLONE_DEST", "Saved_Jobs")
    monkeypatch.chdir(tmp_path)

    # Populate saved/ with at least one file so the command proceeds
    (tmp_path / "saved").mkdir(parents=True)
    export.write_job_markdown(stub_job, tmp_path / "saved")

    rc = main([
        "--resume", str(sample_profile_path),
        "rclone",
    ])
    assert rc == 0

    log = record.read_text(encoding="utf-8")
    # The command we built: rclone sync <src>/ <remote>:<dest>/ -v --delete-during
    assert "sync" in log
    assert "saved/" in log
    assert "gdrive:Saved_Jobs/" in log
    assert "-v" in log
    assert "--delete-during" in log


def test_rclone_subcommand_propagates_rclone_failure(
    tmp_path: Path, monkeypatch, sample_profile_path: Path, stub_job
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _make_fake_rclone(fake_bin, exit_code=7)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    monkeypatch.chdir(tmp_path)
    (tmp_path / "saved").mkdir(parents=True)
    export.write_job_markdown(stub_job, tmp_path / "saved")

    rc = main([
        "--resume", str(sample_profile_path),
        "rclone",
    ])
    assert rc != 0  # subprocess.run(check=True) raises CalledProcessError → SystemExit(1)
