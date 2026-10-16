"""End-to-end orchestration: discover → normalize → dedup → filter → match.

Each step is a small function with a typed return value, so the CLI can
compose them and tests can substitute any step. The orchestrator reports
a :class:`RunReport` capturing counts and diagnostics from every stage.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from jobsearch.dedup import dedupe, to_stored
from jobsearch.errors import SourceError
from jobsearch.filtering import FilterStats, filter_jobs, merge_level_aliases
from jobsearch.matching import rank_matches
from jobsearch.models import JobMatch, RawJob, StoredJob
from jobsearch.profile import CandidateProfile
from jobsearch.queries import build_queries
from jobsearch.sources import SourceAdapter, build_ats_source, build_source
from jobsearch.sources.base import SourceDiagnostic, SourceRequest
from jobsearch.sources.jobspy_source import JOBSPY_SITES


@dataclass
class RunReport:
    queries: dict[str, str] = field(default_factory=dict)
    diagnostics: list[SourceDiagnostic] = field(default_factory=list)
    discovered: int = 0
    deduplicated: int = 0
    filtered_out: int = 0
    matched: int = 0
    filter_stats: FilterStats = field(default_factory=FilterStats)

    def add_diagnostic(self, diagnostic: SourceDiagnostic) -> None:
        self.diagnostics.append(diagnostic)


def build_source_requests(
    profile: CandidateProfile, sites: list[str]
) -> list[tuple[SourceAdapter, SourceRequest]]:
    """Construct an (adapter, request) pair per site and search location.

    Boards are queried once per entry in ``search.locations``; ATS feeds
    ignore location, so they are queried once. Sites whose ATS token is
    missing are skipped here and reported by :func:`skipped_sources`.
    """
    queries = build_queries(profile)
    locations = profile.locations or (profile.location,)
    out: list[tuple[SourceAdapter, SourceRequest]] = []
    for site in sites:
        site_locations = locations if site in JOBSPY_SITES else locations[:1]
        for location in site_locations:
            adapter = _adapter_for(site)
            if not adapter:
                continue
            label = (
                f"{site} @ {location}"
                if site in JOBSPY_SITES and len(locations) > 1
                else site
            )
            out.append((adapter, _request(profile, site, location, queries, label)))
    return out


def skipped_sources(sites: list[str]) -> list[SourceDiagnostic]:
    """Diagnostics for requested ATS sites that have no board token set."""
    return [
        SourceDiagnostic(
            source=site,
            jobs_found=0,
            error=f"skipped: set JOBSEARCH_{site.upper()}_BOARD to a company board token",
        )
        for site in sites
        if site not in JOBSPY_SITES and _adapter_for(site) is None
    ]


def _request(
    profile: CandidateProfile,
    site: str,
    location: str,
    queries: dict[str, str],
    label: str,
) -> SourceRequest:
    form = "indeed" if site == "indeed" else "plain"
    query = queries[form]
    return SourceRequest(
        query=query,
        queries=(query,),
        location=location,
        country=profile.jobspy_country,
        hours_old=profile.hours_old if not (site == "indeed" and not profile.indeed_date_filter) else None,
        results_wanted=profile.results_per_site,
        extra={
            "employment_type": profile.employment_types[0] if profile.employment_types else "",
            "jobspy_country": profile.jobspy_country,
            "linkedin_fetch_description": profile.linkedin_fetch_description,
            "label": label,
        },
    )


def _adapter_for(site: str) -> SourceAdapter | None:
    """Resolve a source name to an adapter instance.

    ATS sources require a board token from the environment. We look for
    ``JOBSEARCH_<NAME>_BOARD`` first, then fall back to none. The board
    token only matters for ATS sources; jobspy sources ignore it.
    """
    if site in JOBSPY_SITES:
        return build_source(site)
    env_var = f"JOBSEARCH_{site.upper()}_BOARD"
    token = os.environ.get(env_var, "").strip()
    if token:
        return build_ats_source(site, token)
    return None


def _fetch_with_retries(
    adapter: SourceAdapter,
    request: SourceRequest,
    retries: int,
    backoff: float,
    sleep: Callable[[float], None],
) -> tuple[list[RawJob], SourceDiagnostic]:
    """Fetch from one source, retrying on errors *and* on empty results.

    Boards throttle by returning nothing rather than failing (the same
    Indeed query returned 5 rows one minute and 0 the next), so an empty
    result is retried too. Backoff is linear: ``backoff * attempt``.
    """
    started = time.monotonic()
    last_error: str | None = None
    attempts = 0
    found: list[RawJob] = []
    for attempt in range(retries + 1):
        attempts = attempt + 1
        try:
            found = list(adapter.fetch(request))
            last_error = None
        except SourceError as exc:
            last_error = str(exc)
            found = []
        except Exception as exc:  # noqa: BLE001 - boundary, recorded as diagnostic
            last_error = f"unexpected error: {exc}"
            found = []
        if found:
            break
        if attempt < retries:
            sleep(backoff * (attempt + 1))
    notes = [f"elapsed={time.monotonic() - started:.1f}s"]
    if attempts > 1:
        notes.append(f"attempts={attempts}")
    diagnostic = SourceDiagnostic(
        source=request.extra.get("label") or adapter.name,
        jobs_found=len(found),
        error=last_error,
        notes=tuple(notes),
    )
    return found, diagnostic


def run_pipeline(
    profile: CandidateProfile,
    sites: list[str] | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[StoredJob], list[JobMatch], RunReport]:
    """Discover → dedupe → filter → match. Returns kept jobs and ranked matches.

    ``sites`` defaults to the profile's ``search.sites``.
    """
    report = RunReport(queries=build_queries(profile))
    chosen = list(sites or profile.sites)
    pairs = build_source_requests(profile, chosen)

    def _one(pair: tuple[SourceAdapter, SourceRequest]):
        adapter, request = pair
        return _fetch_with_retries(
            adapter, request, profile.retries, profile.retry_backoff_seconds, sleep
        )

    # Results are collected in request order, so dedup ("first one wins")
    # stays deterministic whatever order the threads finish in.
    if profile.parallel_sources > 1 and len(pairs) > 1:
        with ThreadPoolExecutor(max_workers=profile.parallel_sources) as pool:
            outcomes = list(pool.map(_one, pairs))
    else:
        outcomes = [_one(pair) for pair in pairs]

    raws: list[RawJob] = []
    for found, diagnostic in outcomes:
        report.add_diagnostic(diagnostic)
        raws.extend(found)
    for diagnostic in skipped_sources(chosen):
        report.add_diagnostic(diagnostic)
    report.discovered = len(raws)

    unique_raws = dedupe(raws)
    report.deduplicated = len(unique_raws)

    stored = [to_stored(raw) for raw in unique_raws]
    filtered, stats = filter_jobs(
        stored,
        hours_old=profile.hours_old,
        exclude_keywords=profile.exclude_keywords,
        level_filter=profile.level_filter,
        exclude_stack=profile.exclude_stack,
        level_aliases=merge_level_aliases(profile.level_aliases),
        max_required_years=profile.max_required_years,
        level_min=profile.level_min,
        allowed_regions=profile.allowed_regions,
        keep_remote=profile.keep_remote,
        keep_unknown_location=profile.keep_unknown_location,
        country=profile.country,
        level_strict=profile.level_strict,
    )
    report.filter_stats = stats
    report.filtered_out = stats.dropped

    matches = rank_matches(filtered, profile)
    report.matched = len(matches)
    return filtered, matches, report
