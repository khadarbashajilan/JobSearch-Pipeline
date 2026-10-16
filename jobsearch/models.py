"""Canonical data model used throughout the pipeline.

The pipeline's value comes from giving every layer one vocabulary, so this
file defines the data shapes the rest of the project talks in. Sources
return :class:`RawJob`. Matching produces :class:`JobMatch`. SQLite persists
:class:`StoredJob`. They are dataclasses so they are cheap to construct in
tests and obvious to read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class WorkArrangement(str, Enum):
    UNKNOWN = "unknown"
    ONSITE = "onsite"
    REMOTE = "remote"
    HYBRID = "hybrid"


class EmploymentType(str, Enum):
    UNKNOWN = "unknown"
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    TEMPORARY = "temporary"
    INTERNSHIP = "internship"
    OTHER = "other"


class LinkStatus(str, Enum):
    """Evidence-backed state of an application URL.

    - ``verified``   : the URL resolves, the page corresponds to a real
      vacancy, and we have positive evidence it is open.
    - ``unverified`` : we have not checked, or the check was inconclusive.
    - ``expired``    : the destination clearly indicates the vacancy is closed.
    - ``unreachable``: the destination could not be reached over the network.
    - ``uncertain``  : reachable but evidence was ambiguous (generic careers
      page, captcha wall, etc.) — flagged so the report explains why.
    """

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    EXPIRED = "expired"
    UNREACHABLE = "unreachable"
    UNCERTAIN = "uncertain"


class ApplicationStatus(str, Enum):
    NEW = "new"
    SAVED = "saved"
    APPLIED = "applied"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"
    CLOSED = "closed"


@dataclass(frozen=True)
class RawJob:
    """A normalized job record produced by a single source adapter.

    Only fields that the source actually supplied are populated; everything
    else stays ``None``. Callers must never invent values for empty fields.
    """

    source: str
    source_id: str | None
    title: str
    company: str | None
    location: str | None
    work_arrangement: WorkArrangement
    employment_type: EmploymentType
    seniority: str | None = None
    description: str | None = None
    required_skills: tuple[str, ...] = ()
    preferred_skills: tuple[str, ...] = ()
    education_requirements: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    source_url: str = ""
    official_url: str | None = None
    apply_url: str | None = None
    date_posted: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JobMatch:
    """A ranked match between a job and the candidate's profile."""

    job: "StoredJob"
    score: float  # 0.0 - 1.0
    matched_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    hard_conflicts: tuple[str, ...]
    explanation: str


@dataclass
class StoredJob:
    """Persistent form of a job, ready for SQLite.

    ``first_seen`` and ``last_seen`` track when *we* observed the job, which
    is distinct from ``date_posted`` (when the employer posted it).
    """

    id: str  # sha1 of (source + source_id + canonical_url), stable across runs
    source: str
    title: str
    company: str | None = None
    location: str | None = None
    source_id: str | None = None
    work_arrangement: WorkArrangement = WorkArrangement.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    seniority: str | None = None
    description: str | None = None
    required_skills: tuple[str, ...] = ()
    preferred_skills: tuple[str, ...] = ()
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    source_url: str = ""
    official_url: str | None = None
    apply_url: str | None = None
    date_posted: datetime | None = None
    first_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    link_status: LinkStatus = LinkStatus.UNVERIFIED
    link_evidence: str | None = None
    link_checked_at: datetime | None = None
    application_status: ApplicationStatus = ApplicationStatus.NEW

    def to_row(self) -> dict[str, Any]:
        """Serialize to a dict matching the SQLite schema column names."""
        return {
            "id": self.id,
            "source": self.source,
            "source_id": self.source_id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "work_arrangement": self.work_arrangement.value,
            "employment_type": self.employment_type.value,
            "seniority": self.seniority,
            "description": self.description,
            "required_skills": ",".join(self.required_skills),
            "preferred_skills": ",".join(self.preferred_skills),
            "salary_min": self.salary_min,
            "salary_max": self.salary_max,
            "salary_currency": self.salary_currency,
            "source_url": self.source_url,
            "official_url": self.official_url,
            "apply_url": self.apply_url,
            "date_posted": self.date_posted.isoformat() if self.date_posted else None,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "link_status": self.link_status.value,
            "link_evidence": self.link_evidence,
            "link_checked_at": (
                self.link_checked_at.isoformat() if self.link_checked_at else None
            ),
            "application_status": self.application_status.value,
        }