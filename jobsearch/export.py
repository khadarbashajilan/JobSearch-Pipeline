"""Write a single job to a markdown file under ``saved/``.

Pure function. No CLI, no rclone, no subprocesses — that's the responsibility
of the caller. Keeping this module small and side-effect-bounded means it
tests cleanly and can be reused from anywhere (CLI, tests, future API).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from jobsearch.links import dork_query, google_dork_url, google_search_url, plain_query


class _JobLike(Protocol):
    """Anything that quacks like a StoredJob for export purposes.

    Using a Protocol (rather than importing StoredJob directly) means this
    module has no dependency on the rest of the package and can render any
    object with the same fields — useful for tests with a stub.
    """

    id: str
    title: str
    company: str | None
    location: str | None
    source: str
    description: str | None
    source_url: str
    official_url: str | None
    apply_url: str | None
    date_posted: datetime | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    application_status: object  # ApplicationStatus enum, but we only str() it


_DESCRIPTION_LIMIT = 4000
_FRONTMATTER_SENTINEL = "---"


def _format_salary(job) -> str:
    if job.salary_min is None and job.salary_max is None:
        return ""
    currency = job.salary_currency or ""
    lo = f"{job.salary_min:,.0f}" if job.salary_min is not None else "?"
    hi = f"{job.salary_max:,.0f}" if job.salary_max is not None else "?"
    prefix = f"{currency} " if currency else ""
    if job.salary_min is not None and job.salary_max is not None:
        return f"{prefix}{lo}-{hi}"
    return f"{prefix}{lo if job.salary_min is not None else hi}"


def _format_date(value) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)


def _best_url(job) -> str:
    """Return the link to the vacancy itself.

    ``official_url`` is the employer's page (often a board company profile),
    so it is never used as the job link.
    """
    return job.apply_url or job.source_url or ""


def _render(job) -> str:
    """Build the markdown text for a job. No I/O."""
    saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    status = str(getattr(job.application_status, "value", job.application_status))
    salary = _format_salary(job)
    url = _best_url(job)
    description = (job.description or "").strip()
    if len(description) > _DESCRIPTION_LIMIT:
        description = description[: _DESCRIPTION_LIMIT - 1] + "..."

    lines: list[str] = []
    lines.append(_FRONTMATTER_SENTINEL)
    lines.append(f"id: {job.id}")
    lines.append(f"title: {job.title}")
    lines.append(f"company: {job.company or ''}")
    lines.append(f"location: {job.location or ''}")
    lines.append(f"site: {job.source}")
    lines.append(f"url: {url}")
    lines.append(f"date_posted: {_format_date(job.date_posted)}")
    lines.append(f"status: {status}")
    lines.append(f"saved_at: {saved_at}")
    lines.append(_FRONTMATTER_SENTINEL)
    lines.append("")
    lines.append(f"# {job.title} - {job.company or '(unknown company)'}")
    lines.append("")
    lines.append(f"- **Location:** {job.location or 'unknown'}")
    lines.append(f"- **Site:** {job.source}")
    lines.append(f"- **Posted:** {_format_date(job.date_posted)}")
    if salary:
        lines.append(f"- **Salary:** {salary}")
    lines.append(f"- **Status:** {status}")
    if url:
        lines.append(f"- **Link:** {url}")
    if job.company or job.title:
        lines.append(f"- **Google search:** [{plain_query(job)}]({google_search_url(job)})")
        lines.append(f"- **Google dork:** [{dork_query(job)}]({google_dork_url(job)})")
    lines.append("")
    lines.append("## Description")
    lines.append("")
    lines.append(description or "_(no description provided)_")
    lines.append("")
    return "\n".join(lines)


def write_job_markdown(job, dest_dir: Path) -> Path:
    """Write a job to ``<dest_dir>/<job.id>.md`` and return the path.

    Creates ``dest_dir`` if missing. Overwrites any existing file for the same
    job id - the markdown is a derived view of the SQLite record, and we want
    the latest version on disk.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{job.id}.md"
    path.write_text(_render(job), encoding="utf-8")
    return path
