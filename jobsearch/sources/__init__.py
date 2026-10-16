"""Source adapters package — re-exports for convenience."""

from jobsearch.sources.ats_json import (
    AshbySource,
    GreenhouseSource,
    LeverSource,
    build_ats_source,
)
from jobsearch.sources.base import SourceAdapter, SourceDiagnostic, SourceRequest
from jobsearch.sources.jobspy_source import JobSpySource, build_source

__all__ = [
    "AshbySource",
    "GreenhouseSource",
    "JobSpySource",
    "LeverSource",
    "SourceAdapter",
    "SourceDiagnostic",
    "SourceRequest",
    "build_ats_source",
    "build_source",
]