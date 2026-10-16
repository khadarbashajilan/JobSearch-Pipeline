"""End-to-end orchestration: discover → normalize → dedup → filter → match.

Each step is a small function with a typed return value, so the CLI can
compose them and tests can substitute any step. The orchestrator reports
a :class:`RunReport` capturing counts and diagnostics from every stage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from jobsearch.dedup import dedupe, to_stored
from jobsearch.errors import SourceError
from jobsearch.filtering import filter_jobs
from jobsearch.matching import rank_matches
from jobsearch.models import JobMatch, RawJob, StoredJob
from jobsearch.profile import CandidateProfile
from jobsearch.queries import build_queries
from jobsearch.sources import SourceAdapter
from jobsearch.sources.base import SourceDiagnostic, SourceRequest


@dataclass
class RunReport:
    queries: dict[str, str] = field(default_factory=dict)
    diagnostics: list[SourceDiagnostic] = field(default_factory=list)
    discovered: int = 0
    deduplicated: int = 0
    filtered_out: int = 0
    matched: int = 0

    def add_diagnostic(self, diagnostic: SourceDiagnostic) -> None:
        self.diagnostics.append(diagnostic)


def build_source_requests(
    profile: CandidateProfile, sites: list[str]
) -> list[tuple[SourceAdapter, SourceRequest]]:
    """Construct a (adapter, request) pair for each requested site."""
    queries = build_queries(profile)
    plain_queries = ("plain",)
    boolean_queries = ("indeed",)

    out: list[tuple[SourceAdapter, SourceRequest]] = []
    for site in sites:
        adapter = _adapter_for(site)
        if not adapter:
            continue
        form = "indeed" if site == "indeed" else "plain"
        query = queries[form]
        request = SourceRequest(
            query=query,
            queries=(query,),
            location=profile.location,
            country=profile.jobspy_country,
            hours_old=profile.hours_old if not (site == "indeed" and not profile.indeed_date_filter) else None,
            results_wanted=profile.results_per_site,
            extra={
                "employment_type": profile.employment_types[0] if profile.employment_types else "",
                "jobspy_country": profile.jobspy_country,
            },
        )
        out.append((adapter, request))
    # silence the linter about unused locals — kept explicit for clarity
    _ = plain_queries, boolean_queries
    return out


def _adapter_for(site: str) -> SourceAdapter | None:
    """Resolve a source name to an adapter instance.

    ATS sources require a board token from the environment. We look for
    ``JOBSEARCH_<NAME>_BOARD`` first, then fall back to none. The board
    token only matters for ATS sources; jobspy sources ignore it.
    """
    import os

    from jobsearch.sources import build_ats_source, build_source

    if site in {"linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "bayt", "naukri", "bdjobs"}:
        return build_source(site)
    env_var = f"JOBSEARCH_{site.upper()}_BOARD"
    token = os.environ.get(env_var, "").strip()
    if token:
        return build_ats_source(site, token)
    return None


def run_pipeline(
    profile: CandidateProfile,
    sites: list[str],
) -> tuple[list[StoredJob], list[JobMatch], RunReport]:
    """Discover → dedupe → filter → match. Returns stored jobs and ranked matches."""
    report = RunReport(queries=build_queries(profile))
    raws: list[RawJob] = []
    for adapter, request in build_source_requests(profile, sites):
        started = time.monotonic()
        try:
            found = list(adapter.fetch(request))
        except SourceError as exc:
            report.add_diagnostic(
                SourceDiagnostic(
                    source=adapter.name,
                    jobs_found=0,
                    error=str(exc),
                )
            )
            continue
        except Exception as exc:  # noqa: BLE001 - boundary, recorded as diagnostic
            report.add_diagnostic(
                SourceDiagnostic(
                    source=adapter.name,
                    jobs_found=0,
                    error=f"unexpected error: {exc}",
                )
            )
            continue
        elapsed = time.monotonic() - started
        report.add_diagnostic(
            SourceDiagnostic(
                source=adapter.name,
                jobs_found=len(found),
                notes=(f"elapsed={elapsed:.1f}s",),
            )
        )
        raws.extend(found)
    report.discovered = len(raws)

    unique_raws = dedupe(raws)
    report.deduplicated = len(unique_raws)

    stored = [to_stored(raw) for raw in unique_raws]
    filtered, stats = filter_jobs(
        stored,
        hours_old=profile.hours_old,
        exclude_keywords=profile.exclude_keywords,
    )
    report.filtered_out = (
        stats.dropped_outside_window + stats.dropped_excluded_title
    )

    matches = rank_matches(filtered, profile)
    report.matched = len(matches)
    return filtered, matches, report