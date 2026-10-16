"""SQLite-backed persistence for jobs, search runs, and application status.

A thin layer on top of the stdlib :mod:`sqlite3` module — no ORM, no
migrations framework. Schema versions are tracked in a single
``schema_version`` row and applied in :func:`migrate`.

Layout:

- ``jobs``            one row per unique job id; ``source_url`` is the
  authoritative link, ``apply_url`` is the direct application link.
- ``job_sources``     which sources have reported a given id (so dedup
  is observable).
- ``match_results``   the latest match score and explanation per job.
- ``runs``            a row per CLI invocation with diagnostics summary.
- ``application_status`` per-job status, editable from the CLI.

The database lives at ``~/.jobsearch/state.db`` by default. Override with
the ``JOBSEARCH_DB`` environment variable. ``resume.md`` is *never*
written into the database.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from jobsearch.errors import PersistenceError
from jobsearch.models import (
    ApplicationStatus,
    JobMatch,
    LinkStatus,
    StoredJob,
)

DEFAULT_DB_PATH_FALLBACK = Path.home() / ".jobsearch" / "state.db"


def default_db_path() -> Path:
    """Return the SQLite path the user has configured for this run.

    Resolved each call so tests that override ``JOBSEARCH_DB`` after
    import are honoured.
    """
    return Path(os.environ.get("JOBSEARCH_DB", DEFAULT_DB_PATH_FALLBACK))


CURRENT_SCHEMA_VERSION = 1


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    db_path = path or default_db_path()
    connection = _connect(db_path)
    try:
        migrate(connection)
        yield connection
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        raise PersistenceError(f"database error: {exc}") from exc
    finally:
        connection.close()


def migrate(connection: sqlite3.Connection) -> None:
    """Apply pending schema migrations. Idempotent."""
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if row is None:
        connection.executescript(_SCHEMA_SQL)
        connection.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(CURRENT_SCHEMA_VERSION),),
        )
        return
    version = connection.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    current = int(version["value"]) if version else 0
    if current < CURRENT_SCHEMA_VERSION:
        # No forward migrations yet; schema is at v1.
        connection.execute(
            "UPDATE meta SET value=? WHERE key='schema_version'",
            (str(CURRENT_SCHEMA_VERSION),),
        )


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT,
    title TEXT NOT NULL,
    company TEXT,
    location TEXT,
    work_arrangement TEXT NOT NULL,
    employment_type TEXT NOT NULL,
    seniority TEXT,
    description TEXT,
    required_skills TEXT,
    preferred_skills TEXT,
    salary_min REAL,
    salary_max REAL,
    salary_currency TEXT,
    source_url TEXT NOT NULL,
    official_url TEXT,
    apply_url TEXT,
    date_posted TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    link_status TEXT NOT NULL,
    link_evidence TEXT,
    link_checked_at TEXT,
    application_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_sources (
    job_id TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT,
    source_url TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (job_id, source),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS match_results (
    job_id TEXT PRIMARY KEY,
    score REAL NOT NULL,
    matched_skills TEXT,
    missing_skills TEXT,
    hard_conflicts TEXT,
    explanation TEXT,
    computed_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    location TEXT,
    query TEXT,
    diagnostics TEXT
);

CREATE TABLE IF NOT EXISTS application_status_history (
    job_id TEXT NOT NULL,
    status TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    note TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
"""


def upsert_jobs(jobs: Iterable[StoredJob], path: Path | None = None) -> list[StoredJob]:
    """Insert or update jobs; return the merged stored records."""
    merged: list[StoredJob] = []
    with connect(path) as connection:
        for job in jobs:
            existing = _get_job(connection, job.id)
            if existing is None:
                _insert_job(connection, job)
                merged.append(job)
            else:
                updated = _merge_job(existing, job)
                _update_job(connection, updated)
                merged.append(updated)
    return merged


def record_matches(
    matches: Iterable[JobMatch], path: Path | None = None
) -> None:
    """Persist the latest match score per job."""
    with connect(path) as connection:
        connection.executemany(
            """
            INSERT INTO match_results
                (job_id, score, matched_skills, missing_skills, hard_conflicts, explanation, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                score=excluded.score,
                matched_skills=excluded.matched_skills,
                missing_skills=excluded.missing_skills,
                hard_conflicts=excluded.hard_conflicts,
                explanation=excluded.explanation,
                computed_at=excluded.computed_at
            """,
            (
                (
                    match.job.id,
                    match.score,
                    json.dumps(list(match.matched_skills)),
                    json.dumps(list(match.missing_skills)),
                    json.dumps(list(match.hard_conflicts)),
                    match.explanation,
                    datetime.now(timezone.utc).isoformat(),
                )
                for match in matches
            ),
        )


def start_run(
    *,
    location: str,
    query: str,
    diagnostics: dict,
    path: Path | None = None,
) -> int:
    """Begin a run record; return its id."""
    with connect(path) as connection:
        cursor = connection.execute(
            "INSERT INTO runs (started_at, location, query, diagnostics) VALUES (?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                location,
                query,
                json.dumps(diagnostics),
            ),
        )
        return int(cursor.lastrowid)


def finish_run(run_id: int, path: Path | None = None) -> None:
    """Mark a run as finished."""
    with connect(path) as connection:
        connection.execute(
            "UPDATE runs SET finished_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), run_id),
        )


def update_status(
    job_id: str,
    status: ApplicationStatus,
    *,
    note: str | None = None,
    path: Path | None = None,
) -> None:
    """Record a user-driven application status change."""
    with connect(path) as connection:
        row = connection.execute(
            "SELECT id FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if row is None:
            raise PersistenceError(f"unknown job id: {job_id}")
        connection.execute(
            "UPDATE jobs SET application_status=? WHERE id=?",
            (status.value, job_id),
        )
        connection.execute(
            """
            INSERT INTO application_status_history (job_id, status, changed_at, note)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, status.value, datetime.now(timezone.utc).isoformat(), note),
        )


def list_jobs(
    *,
    statuses: tuple[ApplicationStatus, ...] = (),
    limit: int = 100,
    path: Path | None = None,
) -> list[StoredJob]:
    """Return stored jobs, optionally filtered by application status."""
    query = "SELECT * FROM jobs"
    params: list = []
    if statuses:
        placeholders = ",".join("?" * len(statuses))
        query += f" WHERE application_status IN ({placeholders})"
        params.extend(s.value for s in statuses)
    query += " ORDER BY last_seen DESC LIMIT ?"
    params.append(limit)
    with connect(path) as connection:
        rows = connection.execute(query, params).fetchall()
    return [_row_to_job(row) for row in rows]


def _get_job(connection: sqlite3.Connection, job_id: str) -> StoredJob | None:
    row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def _insert_job(connection: sqlite3.Connection, job: StoredJob) -> None:
    connection.execute(
        """
        INSERT INTO jobs (
            id, source, source_id, title, company, location, work_arrangement,
            employment_type, seniority, required_skills, preferred_skills,
            salary_min, salary_max, salary_currency, source_url, official_url,
            apply_url, date_posted, first_seen, last_seen, link_status,
            link_evidence, link_checked_at, application_status, description
        ) VALUES (
            :id, :source, :source_id, :title, :company, :location, :work_arrangement,
            :employment_type, :seniority, :required_skills, :preferred_skills,
            :salary_min, :salary_max, :salary_currency, :source_url, :official_url,
            :apply_url, :date_posted, :first_seen, :last_seen, :link_status,
            :link_evidence, :link_checked_at, :application_status, :description
        )
        """,
        job.to_row(),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO job_sources (job_id, source, source_id, source_url, first_seen)
        VALUES (?, ?, ?, ?, ?)
        """,
        (job.id, job.source, job.source_id, job.source_url, job.first_seen.isoformat()),
    )


def _update_job(connection: sqlite3.Connection, job: StoredJob) -> None:
    connection.execute(
        """
        UPDATE jobs SET
            source=:source,
            source_id=:source_id,
            title=:title,
            company=:company,
            location=:location,
            work_arrangement=:work_arrangement,
            employment_type=:employment_type,
            seniority=:seniority,
            required_skills=:required_skills,
            preferred_skills=:preferred_skills,
            salary_min=:salary_min,
            salary_max=:salary_max,
            salary_currency=:salary_currency,
            source_url=:source_url,
            official_url=:official_url,
            apply_url=:apply_url,
            date_posted=:date_posted,
            last_seen=:last_seen,
            link_status=:link_status,
            link_evidence=:link_evidence,
            link_checked_at=:link_checked_at,
            application_status=:application_status,
            description=:description
        WHERE id=:id
        """,
        job.to_row(),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO job_sources (job_id, source, source_id, source_url, first_seen)
        VALUES (?, ?, ?, ?, ?)
        """,
        (job.id, job.source, job.source_id, job.source_url, job.first_seen.isoformat()),
    )


def _merge_job(existing: StoredJob, fresh: StoredJob) -> StoredJob:
    """Combine an existing record with a fresh observation.

    Fields the existing record had stay unless the new one explicitly
    overrides them; ``first_seen`` stays fixed, ``last_seen`` advances.
    """
    def first_or(value: object, fallback: object) -> object:
        return value if value not in (None, "") else fallback

    return StoredJob(
        id=existing.id,
        source=fresh.source,
        source_id=fresh.source_id or existing.source_id,
        title=first_or(fresh.title, existing.title) or "",
        company=first_or(fresh.company, existing.company),
        location=first_or(fresh.location, existing.location),
        work_arrangement=fresh.work_arrangement,
        employment_type=fresh.employment_type,
        seniority=first_or(fresh.seniority, existing.seniority),
        description=first_or(fresh.description, existing.description),
        required_skills=fresh.required_skills or existing.required_skills,
        preferred_skills=fresh.preferred_skills or existing.preferred_skills,
        salary_min=fresh.salary_min or existing.salary_min,
        salary_max=fresh.salary_max or existing.salary_max,
        salary_currency=first_or(fresh.salary_currency, existing.salary_currency),
        source_url=fresh.source_url or existing.source_url,
        official_url=first_or(fresh.official_url, existing.official_url),
        apply_url=first_or(fresh.apply_url, existing.apply_url),
        date_posted=existing.date_posted or fresh.date_posted,
        first_seen=existing.first_seen,
        last_seen=datetime.now(timezone.utc),
        link_status=existing.link_status,
        link_evidence=existing.link_evidence,
        link_checked_at=existing.link_checked_at,
        application_status=existing.application_status,
    )


def _row_to_job(row: sqlite3.Row) -> StoredJob:
    return StoredJob(
        id=row["id"],
        source=row["source"],
        source_id=row["source_id"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        work_arrangement=_enum_value(row["work_arrangement"], LinkStatus, "work_arrangement"),
        employment_type=_enum_value(row["employment_type"], ApplicationStatus, "employment_type"),
        seniority=row["seniority"],
        description=row["description"],
        required_skills=_skills(row["required_skills"]),
        preferred_skills=_skills(row["preferred_skills"]),
        salary_min=row["salary_min"],
        salary_max=row["salary_max"],
        salary_currency=row["salary_currency"],
        source_url=row["source_url"],
        official_url=row["official_url"],
        apply_url=row["apply_url"],
        date_posted=_datetime(row["date_posted"]),
        first_seen=_datetime(row["first_seen"]) or datetime.now(timezone.utc),
        last_seen=_datetime(row["last_seen"]) or datetime.now(timezone.utc),
        link_status=_enum_value(row["link_status"], LinkStatus, "link_status"),
        link_evidence=row["link_evidence"],
        link_checked_at=_datetime(row["link_checked_at"]),
        application_status=_enum_value(
            row["application_status"], ApplicationStatus, "application_status"
        ),
    )


def _enum_value(value: str, enum_cls: type, column: str):
    """Best-effort coercion of a stored string to an enum value.

    We never raise here — a corrupted database row still lets the user
    browse the rest of their data.
    """
    try:
        return enum_cls(value)
    except ValueError:
        # Fall back to the first member so the row stays representable.
        # ``LinkStatus`` and ``ApplicationStatus`` both have a safe default.
        return next(iter(enum_cls))


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _skills(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part for part in value.split(",") if part)