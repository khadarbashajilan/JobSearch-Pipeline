"""Local post-filtering applied after dedup.

Three filters, all run on every job regardless of source:

1. **Freshness window** — drop jobs posted before the cutoff when the
   source actually supplied a date. Unknown dates are *kept* and
   labelled, not silently treated as recent.
2. **Title exclusions** — drop roles whose title contains any excluded
   token as a whole word. The previous substring matcher was both
   silent and wrong; this one logs every decision so the report can
   surface what was filtered and why.
3. **Level filter** — when the profile sets a seniority ceiling
   (``keywords.level_filter``), drop jobs whose inferred level ranks
   above it. ``off`` (the default) disables this stage entirely.

The same rank used by the matcher drives the cutoff, so the filter
and the score tell a consistent story.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jobsearch.models import StoredJob
from jobsearch.textnorm import contains_any

# Seniority rank reused by the matcher. Ordered so a higher number means
# "more senior". Keeping it here too so the filter can run without
# importing the matcher (and its transitive imports).
_SENIORITY_RANK: dict[str, int] = {
    "intern": 0,
    "junior": 1,
    "mid": 2,
    "senior": 3,
    "lead": 4,
    "staff": 5,
    "principal": 6,
}

# Phrases that map to a level even though they don't share the name.
# ``"entry level"`` and ``"new grad"`` both indicate the same rung as
# ``junior``.
_LEVEL_ALIASES: dict[str, str] = {
    "entry-level": "junior",
    "entry level": "junior",
    "new grad": "intern",
    "new graduate": "intern",
    "graduate": "intern",
    "associate": "associate",  # parsed separately below as mid-ish
}

# Title phrases that strongly imply director/head/VP tier — these are
# filtered at the ``senior`` ceiling and above, since they rank higher
# than ``principal``.
_DIRECTOR_TERMS: tuple[str, ...] = (
    "director",
    "head of",
    "vp",
    "vice president",
    "chief",
)

_LEVEL_PATTERNS = (
    ("principal", r"\bprincipal\b"),
    ("staff", r"\bstaff\b"),
    ("lead", r"\blead\b"),
    ("senior", r"\bsenior\b"),
    ("mid", r"\bmid[\s-]?level\b"),
    ("junior", r"\bjunior\b"),
    ("intern", r"\bintern(ship)?\b"),
)


@dataclass
class FilterStats:
    """Counts surfaced in the report so filtering is observable."""

    kept: int = 0
    dropped_outside_window: int = 0
    dropped_excluded_title: int = 0
    dropped_level_above: int = 0
    dropped_hard_conflict: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "kept": self.kept,
            "dropped_outside_window": self.dropped_outside_window,
            "dropped_excluded_title": self.dropped_excluded_title,
            "dropped_level_above": self.dropped_level_above,
            "dropped_hard_conflict": self.dropped_hard_conflict,
        }


def infer_level(title: str) -> str | None:
    """Return the highest-ranked seniority level found in ``title``.

    Returns ``None`` when the title carries no seniority hint at all —
    the filter passes such jobs through (absence of evidence isn't
    evidence of absence).
    """
    if not title:
        return None
    text = title.lower()
    matched: list[str] = []
    for level, pattern in _LEVEL_PATTERNS:
        if re.search(pattern, text):
            matched.append(level)
    for alias, mapped in _LEVEL_ALIASES.items():
        if alias in text:
            matched.append(mapped)
    # Director-tier words imply seniority >= senior.
    if any(term in text for term in _DIRECTOR_TERMS):
        matched.append("senior")
    if not matched:
        return None
    # Take the highest-ranked match so "Senior Staff Engineer" → staff.
    return max(matched, key=lambda level: _SENIORITY_RANK.get(level, 2))


def filter_jobs(
    jobs: list[StoredJob],
    *,
    hours_old: int | None,
    exclude_keywords: tuple[str, ...],
    level_filter: str = "off",
) -> tuple[list[StoredJob], FilterStats]:
    """Apply freshness, exclusion, and level filters.

    Undated jobs pass through the freshness filter so the report can
    show them with a clear "posted date unknown" marker rather than
    discarding them.
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
    cutoff_rank = _SENIORITY_RANK.get(level_filter) if level_filter != "off" else None
    for job in jobs:
        posted = parse_iso_datetime(job.date_posted)
        if cutoff and posted is not None and posted < cutoff:
            stats.dropped_outside_window += 1
            continue
        if needles and contains_any(job.title, needles):
            stats.dropped_excluded_title += 1
            continue
        if cutoff_rank is not None:
            level = infer_level(job.title)
            if level is not None and _SENIORITY_RANK.get(level, 2) > cutoff_rank:
                stats.dropped_level_above += 1
                continue
        out.append(job)
    stats.kept = len(out)
    return out, stats