"""Markdown report rendering.

The report is the only artifact a user reads, so it earns a dedicated
module. It is intentionally text-only — no HTML, no live links to
private state — and it surfaces every diagnostic honestly, including
zeros and unverified URLs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from jobsearch.filtering import FilterStats, infer_level, merge_level_aliases
from jobsearch.links import dork_query, google_dork_url, google_search_url, plain_query
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
        f"**Searched:** {_cell(', '.join(profile.locations or (profile.location,)))} · **Country:** {_cell(profile.jobspy_country)}",
        "",
    ]
    if profile.allowed_regions:
        lines += [f"**Kept regions:** {_cell(', '.join(profile.allowed_regions))}", ""]
    lines += [
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
    if profile.exclude_stack:
        applied.append("stack exclusions: " + ", ".join(profile.exclude_stack))
    band = _level_band(profile)
    if band:
        applied.append(band)
    if profile.max_required_years is not None:
        applied.append(f"max {profile.max_required_years:g} yrs required")
    if applied:
        lines.append("**Applied locally:** " + " · ".join(applied))
        lines.append("")
    dropped = _dropped_breakdown(filter_stats)
    if dropped:
        lines.append("**Dropped:** " + " · ".join(dropped))
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

    aliases = merge_level_aliases(profile.level_aliases)
    fresh = [m for m in visible if m.job.id not in seen_ids]
    returning = [m for m in visible if m.job.id in seen_ids]
    if profile.report_seen == "full":
        fresh, returning = visible, []

    lines.append(f"## New ({len(fresh)})" if profile.report_seen != "full" else f"## Ranked shortlist ({len(fresh)})")
    lines.append("")
    if not fresh:
        lines += ["_Nothing new since the last run._", ""]
    for index, match in enumerate(fresh, start=1):
        is_returning = match.job.id in seen_ids
        lines.extend(_render_job(index, match.job, match, is_returning, aliases))
        lines.append("")

    if returning and profile.report_seen == "compact":
        lines.append(f"## Previously seen ({len(returning)})")
        lines.append("")
        for match in returning:
            lines.append(_render_compact(match))
        lines.append("")
    elif returning and profile.report_seen == "hide":
        lines.append(f"_{len(returning)} previously seen job(s) hidden (report.previously_seen = \"hide\")._")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _dropped_breakdown(stats: FilterStats) -> list[str]:
    labels = (
        ("dropped_outside_window", "too old"),
        ("dropped_excluded_title", "title excluded"),
        ("dropped_excluded_stack", "stack excluded"),
        ("dropped_level_above", "above level"),
        ("dropped_level_below", "below level"),
        ("dropped_level_unknown", "level not stated (strict)"),
        ("dropped_location", "outside regions"),
        ("dropped_experience_above", "too many years required"),
        ("dropped_hard_conflict", "hard conflict"),
    )
    return [f"{getattr(stats, attr)} {label}" for attr, label in labels if getattr(stats, attr)]


def _level_band(profile: CandidateProfile) -> str:
    low = profile.level_min if profile.level_min != "off" else ""
    high = profile.level_filter if profile.level_filter != "off" else ""
    if not low and not high:
        return ""
    if low == high:
        text = f"level={low}"
    else:
        text = f"level {low or 'any'}–{high or 'any'}"
    return text + (" (strict)" if profile.level_strict else "")


def _job_link(job: StoredJob) -> str:
    """Link to the vacancy itself; ``official_url`` is the employer page."""
    return job.apply_url or job.source_url or ""


def _render_compact(match: JobMatch) -> str:
    job = match.job
    title = _cell(job.title) or "untitled"
    url = _job_link(job)
    head = f"[{title}]({_cell(url)})" if url else title
    company = f" · {_cell(job.company)}" if job.company else ""
    status = (
        f" · `{job.application_status.value}`"
        if job.application_status != ApplicationStatus.NEW
        else ""
    )
    return f"- {match.score:.2f} — {head}{company}{status}"


def _render_job(
    index: int,
    job: StoredJob,
    match: JobMatch,
    is_returning: bool,
    aliases: dict[str, str] | None = None,
) -> list[str]:
    title = _cell(job.title) or "untitled"
    url = _job_link(job)
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

    # Surface the inferred seniority so the level-filter knob is auditable
    # in the report — when ``level_filter`` is set, you can see exactly
    # why each kept job was allowed through.
    inferred = infer_level(job.title, aliases)
    if inferred:
        bits.append(f"- level: `{inferred}`")

    bits.append(f"- source: `{job.source}` · verification: `{job.link_status.value}`")
    if job.link_evidence:
        bits.append(f"- link evidence: {_cell(job.link_evidence)}")
    if job.apply_url and job.source_url and job.apply_url != job.source_url:
        bits.append(f"- listing: {_cell(job.source_url)}")
    if job.official_url and job.official_url not in (job.apply_url, job.source_url):
        bits.append(f"- company page: {_cell(job.official_url)}")
    if job.company or job.title:
        bits.append(f"- Google search: [{plain_query(job)}]({google_search_url(job)})")
        bits.append(f"- Google dork: [{dork_query(job)}]({google_dork_url(job)})")
    if job.application_status != ApplicationStatus.NEW:
        bits.append(f"- application status: `{job.application_status.value}`")
    if is_returning:
        bits.append("- previously seen")
    return bits