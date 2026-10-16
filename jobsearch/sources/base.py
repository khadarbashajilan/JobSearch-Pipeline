"""Source adapter contract.

Every job source — boards, ATSes, custom feeds — implements the same small
interface so the orchestrator can run them concurrently and tolerate
individual failures. Adapters are deliberately stateless: they take a
:class:`SourceRequest` and yield :class:`RawJob` records. They never raise
across the boundary on transient problems; instead they report a
:class:`SourceDiagnostic` so the report can show "Greenhouse: 0 (timeout)"
without crashing the rest of the search.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable

from jobsearch.models import RawJob


@dataclass(frozen=True)
class SourceRequest:
    """Inputs every source needs.

    ``query`` is the raw string the source should send. ``queries`` is the
    full set so sources that want to combine terms can. ``location`` and
    ``country`` mirror the candidate profile. ``extra`` is a passthrough for
    source-specific knobs (e.g. ATS board tokens).
    """

    query: str
    queries: tuple[str, ...]
    location: str
    country: str
    hours_old: int | None
    results_wanted: int
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceDiagnostic:
    """Per-source outcome recorded for the report."""

    source: str
    jobs_found: int
    error: str | None = None
    notes: tuple[str, ...] = ()


class SourceAdapter(ABC):
    """Small interface every job source implements."""

    name: str = "unknown"

    @abstractmethod
    def fetch(self, request: SourceRequest) -> Iterable[RawJob]:
        """Yield raw jobs for ``request``.

        Adapters must not raise for empty results or transport failures
        that are part of normal operation. They may raise only for
        programmer errors (bad configuration). Transient failures should
        return zero results and set ``error`` on the diagnostic returned
        from :meth:`diagnose`.
        """

    def diagnose(self, jobs: list[RawJob]) -> SourceDiagnostic:
        """Default diagnostic — adapters may override for richer context."""
        return SourceDiagnostic(source=self.name, jobs_found=len(jobs))