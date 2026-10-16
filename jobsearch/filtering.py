"""Local post-filtering applied after dedup.

Six filters, all run on every job regardless of source:

1. **Freshness window** — drop jobs posted before the cutoff when the
   source actually supplied a date. Unknown dates are *kept* and
   labelled, not silently treated as recent.
2. **Title exclusions** — drop roles whose title contains any excluded
   token as a whole word (``keywords.exclude``).
3. **Stack exclusions** — drop roles whose title names a technology the
   candidate does not work in (``keywords.exclude_stack``).
4. **Level band** — ``keywords.level_filter`` is a ceiling and
   ``keywords.level_min`` a floor; jobs whose inferred level falls
   outside the band are dropped. ``off`` disables either end.
5. **Experience ceiling** — when ``experience.max_required_years`` is
   set, drop jobs whose title or description asks for more years than
   that.
6. **Location** — when ``search.allowed_regions`` is set, drop jobs whose
   location names none of them. Remote jobs and jobs with no location
   are kept unless the profile says otherwise.

The same rank and the same level inference drive the matcher, so the
filter and the score tell a consistent story.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from jobsearch.models import StoredJob, WorkArrangement
from jobsearch.textnorm import contains_any, has_token

# Ordered so a higher number means "more senior".
SENIORITY_RANK: dict[str, int] = {
    "intern": 0,
    "junior": 1,
    "mid": 2,
    "senior": 3,
    "lead": 4,
    "staff": 5,
    "principal": 6,
}
_SENIORITY_RANK = SENIORITY_RANK  # backwards-compatible alias

# An alias value that switches a default alias off from the profile.
IGNORE_LEVEL = "ignore"

# Title phrase → level. These are general hiring conventions, not personal
# preferences, so they ship as defaults; ``[levels.aliases]`` in the profile
# overrides any entry (same key) or adds new ones, and a value of
# ``"ignore"`` disables a default.
DEFAULT_LEVEL_ALIASES: dict[str, str] = {
    "principal": "principal",
    "staff": "staff",
    "lead": "lead",
    "senior": "senior",
    "sr": "senior",
    "sr.": "senior",
    "mid-level": "mid",
    "mid level": "mid",
    "junior": "junior",
    "jr": "junior",
    "jr.": "junior",
    "entry-level": "junior",
    "entry level": "junior",
    "associate": "junior",
    "intern": "intern",
    "internship": "intern",
    # Entry-level full-time wording ("Graduate Engineer Trainee", "Fresher").
    "trainee": "junior",
    "new grad": "junior",
    "new graduate": "junior",
    "graduate": "junior",
    "fresher": "junior",
    "freshers": "junior",
    # Numbered ladders: "Software Engineer II / III / IV".
    "i": "junior",
    "ii": "mid",
    "iii": "senior",
    "iv": "lead",
    # Director-tier titles rank above the IC ladder; treating them as senior
    # means any ceiling below senior drops them.
    "director": "senior",
    "head of": "senior",
    "vp": "senior",
    "vice president": "senior",
    "chief": "senior",
}

# "5+ years", "3-5 yrs", "2 to 4 years", "6+ Years Mandatory".
_YEARS = r"(\d{1,2}(?:\.\d)?)\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b"
_YEARS_TITLE_RE = re.compile(_YEARS, re.I)
# In descriptions, only count numbers tied to "experience" so "a 20 year old
# company" does not read as a requirement.
_YEARS_DESC_RE = re.compile(_YEARS + r"[^.\n]{0,40}?\bexperience", re.I)
_EXPERIENCE_OF_RE = re.compile(r"\bexperience\s+(?:of\s+)?" + _YEARS, re.I)


@dataclass
class FilterStats:
    """Counts surfaced in the report so filtering is observable."""

    kept: int = 0
    dropped_outside_window: int = 0
    dropped_excluded_title: int = 0
    dropped_excluded_stack: int = 0
    dropped_level_above: int = 0
    dropped_level_below: int = 0
    dropped_level_unknown: int = 0
    dropped_experience_above: int = 0
    dropped_location: int = 0
    dropped_hard_conflict: int = 0

    @property
    def dropped(self) -> int:
        return (
            self.dropped_outside_window
            + self.dropped_excluded_title
            + self.dropped_excluded_stack
            + self.dropped_level_above
            + self.dropped_level_below
            + self.dropped_level_unknown
            + self.dropped_experience_above
            + self.dropped_location
            + self.dropped_hard_conflict
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "kept": self.kept,
            "dropped_outside_window": self.dropped_outside_window,
            "dropped_excluded_title": self.dropped_excluded_title,
            "dropped_excluded_stack": self.dropped_excluded_stack,
            "dropped_level_above": self.dropped_level_above,
            "dropped_level_below": self.dropped_level_below,
            "dropped_level_unknown": self.dropped_level_unknown,
            "dropped_experience_above": self.dropped_experience_above,
            "dropped_location": self.dropped_location,
            "dropped_hard_conflict": self.dropped_hard_conflict,
        }


def merge_level_aliases(overrides: Mapping[str, str] | None) -> dict[str, str]:
    """Defaults plus profile overrides, with ``ignore`` entries removed."""
    merged = dict(DEFAULT_LEVEL_ALIASES)
    for phrase, level in (overrides or {}).items():
        merged[phrase.strip().lower()] = level.strip().lower()
    return {k: v for k, v in merged.items() if v != IGNORE_LEVEL}


def infer_level(title: str, aliases: Mapping[str, str] | None = None) -> str | None:
    """Return the highest-ranked seniority level found in ``title``.

    ``aliases`` is the merged phrase → level table (see
    :func:`merge_level_aliases`); ``None`` means the defaults.

    Returns ``None`` when the title carries no seniority hint at all —
    the filter passes such jobs through (absence of evidence isn't
    evidence of absence).
    """
    if not title:
        return None
    table = merge_level_aliases(None) if aliases is None else aliases
    text = title.lower()
    matched = [level for phrase, level in table.items() if has_token(text, phrase)]
    if not matched:
        return None
    # Take the highest-ranked match so "Senior Staff Engineer" → staff.
    return max(matched, key=lambda level: SENIORITY_RANK.get(level, 2))


def required_years(title: str | None, description: str | None) -> float | None:
    """Years of experience a posting asks for, or ``None`` if unstated.

    A number in the title wins (it is the employer's headline). Otherwise
    the *smallest* experience requirement in the description is used, so
    "3+ years Python, 1+ year React" reads as 1 — the lenient reading,
    because over-filtering hides jobs the candidate could still apply to.
    """
    if title:
        match = _YEARS_TITLE_RE.search(title)
        if match:
            return float(match.group(1))
    if description:
        values = [float(m.group(1)) for m in _YEARS_DESC_RE.finditer(description)]
        values += [float(m.group(1)) for m in _EXPERIENCE_OF_RE.finditer(description)]
        if values:
            return min(values)
    return None


def filter_jobs(
    jobs: list[StoredJob],
    *,
    hours_old: int | None,
    exclude_keywords: tuple[str, ...],
    level_filter: str = "off",
    exclude_stack: tuple[str, ...] = (),
    level_aliases: Mapping[str, str] | None = None,
    max_required_years: float | None = None,
    level_min: str = "off",
    allowed_regions: tuple[str, ...] = (),
    keep_remote: bool = True,
    keep_unknown_location: bool = True,
    country: str = "",
    level_strict: bool = False,
) -> tuple[list[StoredJob], FilterStats]:
    """Apply freshness, exclusion, level, and experience filters.

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
    stack = tuple(k.lower() for k in exclude_stack if k)
    aliases = merge_level_aliases(None) if level_aliases is None else level_aliases
    cutoff_rank = SENIORITY_RANK.get(level_filter) if level_filter != "off" else None
    floor_rank = SENIORITY_RANK.get(level_min) if level_min != "off" else None
    regions = tuple(r.lower() for r in allowed_regions if r)
    for job in jobs:
        posted = parse_iso_datetime(job.date_posted)
        if cutoff and posted is not None and posted < cutoff:
            stats.dropped_outside_window += 1
            continue
        if needles and contains_any(job.title, needles):
            stats.dropped_excluded_title += 1
            continue
        if stack and contains_any(job.title, stack):
            stats.dropped_excluded_stack += 1
            continue
        if cutoff_rank is not None or floor_rank is not None:
            level = infer_level(job.title, aliases) or infer_level(job.seniority or "", aliases)
            rank = SENIORITY_RANK.get(level, 2) if level is not None else None
            if rank is not None and cutoff_rank is not None and rank > cutoff_rank:
                stats.dropped_level_above += 1
                continue
            if rank is not None and floor_rank is not None and rank < floor_rank:
                stats.dropped_level_below += 1
                continue
            if level_strict and rank is None and not _years_within(job, max_required_years):
                stats.dropped_level_unknown += 1
                continue
        if max_required_years is not None:
            years = required_years(job.title, job.description)
            if years is not None and years > max_required_years:
                stats.dropped_experience_above += 1
                continue
        if regions and not _location_allowed(
            job, regions, keep_remote, keep_unknown_location, country
        ):
            stats.dropped_location += 1
            continue
        out.append(job)
    stats.kept = len(out)
    return out, stats


def _location_allowed(
    job: StoredJob,
    regions: tuple[str, ...],
    keep_remote: bool,
    keep_unknown: bool,
    country: str = "",
) -> bool:
    """True when the job's location names an allowed region (whole token).

    A location that is only the country ("India", "IN") names no city, so
    it is treated like a missing location.
    """
    location = (job.location or "").strip()
    if country and location.lower().strip(" ,") in {country.lower(), country.lower()[:2]}:
        location = ""
    if keep_remote and (
        job.work_arrangement == WorkArrangement.REMOTE or has_token(location, "remote")
    ):
        return True
    if not location:
        return keep_unknown
    return bool(contains_any(location, regions))


def _years_within(job: StoredJob, max_years: float | None) -> bool:
    """True when the posting states a years requirement within ``max_years``."""
    if max_years is None:
        return False
    years = required_years(job.title, job.description)
    return years is not None and years <= max_years
