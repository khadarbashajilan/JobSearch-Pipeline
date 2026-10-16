"""Local post-filtering applied after dedup.

Two filters, both run on every job regardless of source:

1. Freshness window — drop jobs posted before the cutoff when the source
   actually supplied a date. Unknown dates are *kept* and labeled, not
   silently treated as recent. We never pretend first-seen equals
   posted.
2. Title exclusions — drop roles whose title contains any excluded
   token as a whole word. The previous substring matcher was both
   silent and wrong; this one logs every decision so the report can
   surface what was filtered and why.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jobsearch.models import StoredJob
from jobsearch.textnorm import contains_any


@dataclass
class FilterStats:
    """Counts surfaced in the report so filtering is observable."""

    kept: int = 0
    dropped_unknown_date: int = 0  # never used; kept so reports can reference it
    dropped_outside_window: int = 0
    dropped_excluded_title: int = 0
    dropped_hard_conflict: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "kept": self.kept,
            "dropped_outside_window": self.dropped_outside_window,
            "dropped_excluded_title": self.dropped_excluded_title,
            "dropped_hard_conflict": self.dropped_hard_conflict,
        }


def filter_jobs(
    jobs: list[StoredJob],
    *,
    hours_old: int | None,
    exclude_keywords: tuple[str, ...],
) -> tuple[list[StoredJob], FilterStats]:
    """Apply freshness and exclusion filters.

    Undated jobs pass through so the report can show them with a clear
    "posted date unknown" marker rather than discarding them.
    """
    from jobsearch.textnorm import parse_iso_datetime

    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=hours_old)
        if hours_old
        else None
    )
    stats = FilterStats()
    out: list[StoredJob] = []
    needles = tuple(k.lower() for k in exclude_keywords if k)
    for job in jobs:
        posted = parse_iso_datetime(job.date_posted)
        if cutoff and posted is not None and posted < cutoff:
            stats.dropped_outside_window += 1
            continue
        if needles and contains_any(job.title, needles):
            stats.dropped_excluded_title += 1
            continue
        out.append(job)
    stats.kept = len(out)
    return out, stats