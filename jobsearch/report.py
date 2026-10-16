"""Markdown report rendering.

The report is the only artifact a user reads, so it earns a dedicated
module. It is intentionally text-only — no HTML, no live links to
private state — and it surfaces every diagnostic honestly, including
zeros and unverified URLs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from jobsearch.filtering import FilterStats
from jobsearch.models import (
    ApplicationStatus,
    JobMatch,
    LinkStatus,
    StoredJob,
)
from jobsearch.orchestrate import RunReport
from jobsearch.profile import CandidateProfile


def _md_escape(value: object) -> str:
    """Escape a value for safe use inside a markdown line."""
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("\r", " ")
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _cell(value: object) -> str:
    """Render a dataframe/cell-typed value as markdown-safe text."""
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    return _md_escape(value).strip()


def _format_date(value) -> str:
    if not value:
        return "unknown"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return _cell(value)


def render(
    matches: list[JobMatch],
    profile: CandidateProfile,
    report: RunReport,
    filter_stats: FilterStats,
    previously_seen_ids: Iterable[str] = (),
) -> str:
    """Build a ranked Markdown shortlist with diagnostics."""
    stamp = datetime.now()
    seen_ids = set(previously_seen_ids)
    lines: list[str] = [
        f"# Job Search — {stamp:%Y-%m-%d %H:%M}",
        "",
        f"**Location:** {_cell(profile.location)} · **Country:** {_cell(profile.jobspy_country)}",
        "",
        f"**Titles:** {_cell(profile.primary_title)}"
        + (f" / alt: {_cell(', '.join(profile.alternative_titles))}" if profile.alternative_titles else ""),
        "",
    ]

    lines += [
        f"**Discovered:** {report.discovered} · "
        f"**Unique:** {report.deduplicated} · "
        f"**Filtered out:** {report.filtered_out} · "
        f"**Matched:** {report.matched}",
        "",
    ]

    if report.diagnostics:
        lines.append("## Source diagnostics")
        lines.append("")
        for diag in report.diagnostics:
            base = f"- `{diag.source}`: {diag.jobs_found} jobs"
            if diag.error:
                base += f" — error: {diag.error}"
            if diag.notes:
                base += f" ({'; '.join(diag.notes)})"
            lines.append(base)
        lines.append("")

    if profile.min_score > 0:
        applied = [
            f"minimum score {profile.min_score:.2f}",
        ]
    else:
        applied = []
    if profile.hours_old:
        applied.append(f"posted within last {profile.hours_old}h")
    if profile.exclude_keywords:
        applied.append("title exclusions: " + ", ".join(profile.exclude_keywords))
    if applied:
        lines.append("**Applied locally:** " + " · ".join(applied))
        lines.append("")

    if report.queries:
        lines += ["## Queries", ""]
        for label, value in report.queries.items():
            lines.append(f"- `{label}` → `{value}`")
        lines.append("")

    visible = [m for m in matches if m.score >= profile.min_score]
    if not visible:
        lines += [
            "## No matching jobs",
            "",
            "Either no source returned anything, or every job was filtered out.",
        ]
        return "\n".join(lines) + "\n"

    lines.append(f"## Ranked shortlist ({len(visible)})")
    lines.append("")
    for index, match in enumerate(visible, start=1):
        job = match.job
        lines.extend(_render_job(index, job, match, match.job.id in seen_ids))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_job(
    index: int, job: StoredJob, match: JobMatch, is_returning: bool
) -> list[str]:
    title = _cell(job.title) or "untitled"
    url = job.apply_url or job.official_url or job.source_url
    heading = f"### {index}. [{title}]({_cell(url)})" if url else f"### {index}. {title}"

    bits: list[str] = [heading]
    sub_bits = []
    if job.company:
        sub_bits.append(_cell(job.company))
    if job.location:
        sub_bits.append(_cell(job.location))
    if job.date_posted:
        sub_bits.append("posted " + _format_date(job.date_posted))
    elif job.date_posted is None and (job.first_seen):
        sub_bits.append("posted date unknown")
    if sub_bits:
        bits.append(" · ".join(sub_bits))

    bits.append(f"**Score:** {match.score:.2f}")
    if match.hard_conflicts:
        bits.append("⚠ **Hard conflict:** " + "; ".join(match.hard_conflicts))
    if match.explanation:
        bits.append(f"_{match.explanation}_")

    if match.matched_skills:
        bits.append(f"- matched: {', '.join(match.matched_skills)}")
    if match.missing_skills:
        bits.append(f"- missing: {', '.join(match.missing_skills)}")

    bits.append(f"- source: `{job.source}` · verification: `{job.link_status.value}`")
    if job.link_evidence:
        bits.append(f"- link evidence: {_cell(job.link_evidence)}")
    if job.apply_url and job.apply_url != job.source_url:
        bits.append(f"- apply URL: {_cell(job.apply_url)}")
    elif job.source_url:
        bits.append(f"- source URL: {_cell(job.source_url)}")
    if job.application_status != ApplicationStatus.NEW:
        bits.append(f"- application status: `{job.application_status.value}`")
    if is_returning:
        bits.append("- previously seen")
    return bits