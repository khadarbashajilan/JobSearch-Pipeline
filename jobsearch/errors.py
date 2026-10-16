"""Domain-specific exceptions.

Each module raises its own narrow exception so callers can recover from one
kind of failure without masking another. The base class ``JobSearchError``
makes top-level reporting trivial.
"""

from __future__ import annotations


class JobSearchError(Exception):
    """Base class for everything this project raises deliberately."""


class ProfileError(JobSearchError):
    """Raised when the résumé profile is missing, malformed, or invalid."""


class ResumeParseError(JobSearchError):
    """Raised when a résumé file cannot be read or its structure is unusable."""


class SourceError(JobSearchError):
    """Raised when a single source adapter fails.

    Other adapters continue to run. Surfaced in diagnostics so a flaky source
    is visible rather than silently swallowed.
    """


class VerificationError(JobSearchError):
    """Raised when a URL verifier encounters an unexpected network or parse fault."""


class PersistenceError(JobSearchError):
    """Raised when the SQLite store cannot be opened, migrated, or written."""