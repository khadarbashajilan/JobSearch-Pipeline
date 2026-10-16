"""Cross-source deduplication.

Two jobs are considered duplicates when their *identity* matches, not
just their surface text. The strongest signal is a matching canonical URL.
When URLs differ, we fall back to (source_id, company, normalised title,
location). Same title and company alone are *not* enough — distinct
vacancies frequently share them, and merging them silently would mislead
the user.
"""

from __future__ import annotations

from jobsearch.ids import job_id
from jobsearch.models import RawJob, StoredJob
from jobsearch.normalize import canonicalize_url
from jobsearch.textnorm import normalize_title


def _coerce_str(value: object) -> str:
    """Best-effort coercion to a string for dedup comparisons.

    Some upstream fields occasionally surface as floats (e.g. Naukri's
    ``company_rating``) when the column name is reused; we never want
    such a value to crash the pipeline.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip().lower()
    return str(value).strip().lower()


def canonical_identity(job: RawJob | StoredJob) -> tuple[str, str, str, str, str]:
    """Return the (source, source_id, normalized title, company, location) tuple used for fallback matching."""
    title = normalize_title(getattr(job, "title", "") or "")
    company = _coerce_str(getattr(job, "company", ""))
    location = _coerce_str(getattr(job, "location", ""))
    return (
        getattr(job, "source", ""),
        getattr(job, "source_id", "") or "",
        title,
        company,
        location,
    )


def canonical_url(job: RawJob | StoredJob) -> str:
    """Canonical URL of the job, falling back to its apply URL."""
    return canonicalize_url(
        getattr(job, "source_url", "") or getattr(job, "apply_url", "") or ""
    )


def is_same_vacancy(a: RawJob | StoredJob, b: RawJob | StoredJob) -> bool:
    """Return True when ``a`` and ``b`` are very likely the same posting."""
    # Same ATS id on the same source is decisive on its own — it
    # overrides URL differences because the same ATS can surface a posting
    # under several URLs (referrals, location variants, language packs).
    a_id = getattr(a, "source_id", None)
    b_id = getattr(b, "source_id", None)
    if a.source == b.source and a_id and a_id == b_id:
        return True
    if canonical_url(a) and canonical_url(b):
        return canonical_url(a) == canonical_url(b)
    sa = canonical_identity(a)
    sb = canonical_identity(b)
    if not sa[1] or not sb[1]:
        return False
    return sa == sb


def dedupe(jobs: list[RawJob]) -> list[RawJob]:
    """Remove duplicates, keeping the first occurrence.

    "First" is the order callers passed in, so put the source you trust
    most at the front of the list.
    """
    seen_urls: dict[str, RawJob] = {}
    seen_ats_ids: dict[tuple[str, str], RawJob] = {}
    seen_identities: dict[tuple, RawJob] = {}
    out: list[RawJob] = []
    for job in jobs:
        ats_key = (job.source, job.source_id) if job.source_id else None
        if ats_key and ats_key in seen_ats_ids:
            continue
        url_key = canonical_url(job)
        if url_key and url_key in seen_urls:
            continue
        identity = canonical_identity(job)
        if not url_key and identity[1] and identity in seen_identities:
            continue
        if ats_key:
            seen_ats_ids[ats_key] = job
        if url_key:
            seen_urls[url_key] = job
        if identity[1]:
            seen_identities[identity] = job
        out.append(job)
    return out


def to_stored(job: RawJob) -> StoredJob:
    """Convert a :class:`RawJob` into a :class:`StoredJob` ready for SQLite."""
    stored = StoredJob(
        id=job_id(job.source, job.source_id, job.source_url or job.apply_url or ""),
        source=job.source,
        source_id=job.source_id,
        title=job.title,
        company=job.company,
        location=job.location,
        work_arrangement=job.work_arrangement,
        employment_type=job.employment_type,
        seniority=job.seniority,
        description=job.description,
        required_skills=job.required_skills,
        preferred_skills=job.preferred_skills,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        salary_currency=job.salary_currency,
        source_url=job.source_url,
        official_url=job.official_url,
        apply_url=job.apply_url,
        date_posted=job.date_posted,
    )
    return stored