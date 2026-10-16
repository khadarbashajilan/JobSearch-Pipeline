"""JobSpy-backed source adapter.

Wraps the existing JobSpy library as one of the sources. Keeps the
vendored library in use rather than replacing it, which is the path of
least surprise for users who already trust its board-specific quirks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from jobspy import scrape_jobs as jobspy_scrape_jobs
from jobspy.model import Site

from jobsearch.errors import SourceError
from jobsearch.models import (
    EmploymentType,
    RawJob,
    WorkArrangement,
)
from jobsearch.sources.base import SourceAdapter, SourceDiagnostic, SourceRequest

_SITE_MAP = {
    "linkedin": Site.LINKEDIN,
    "indeed": Site.INDEED,
    "zip_recruiter": Site.ZIP_RECRUITER,
    "glassdoor": Site.GLASSDOOR,
    "google": Site.GOOGLE,
    "bayt": Site.BAYT,
    "naukri": Site.NAUKRI,
    "bdjobs": Site.BDJOBS,
}

JOBSPY_SITES = frozenset(_SITE_MAP)

_EMPLOYMENT_MAP = {
    "full_time": "fulltime",
    "part_time": "parttime",
    "contract": "contract",
    "temporary": "temporary",
    "internship": "internship",
    "other": "other",
}


class JobSpySource(SourceAdapter):
    """One source per JobSpy board. ``name`` is the Site value."""

    def __init__(self, site_token: str):
        if site_token not in _SITE_MAP:
            raise SourceError(f"unsupported jobspy site: {site_token}")
        self.name = site_token
        self._site = _SITE_MAP[site_token]

    def fetch(self, request: SourceRequest) -> Iterable[RawJob]:
        job_type = (
            _EMPLOYMENT_MAP[request.extra["employment_type"]]
            if request.extra.get("employment_type")
            else None
        )
        kwargs = dict(
            site_name=[self._site],
            search_term=request.query,
            location=request.location,
            results_wanted=request.results_wanted,
            hours_old=request.hours_old,
            country_indeed=request.extra.get("jobspy_country", "USA"),
            verbose=0,
        )
        if job_type:
            kwargs["job_type"] = job_type
        if self._site == Site.LINKEDIN and request.extra.get("linkedin_fetch_description"):
            # One extra request per job, but without it LinkedIn rows carry
            # no description, so skills and experience cannot be read.
            kwargs["linkedin_fetch_description"] = True
        try:
            df = jobspy_scrape_jobs(**kwargs)
        except Exception as exc:  # JobSpy boards are flaky by design
            raise SourceError(f"{self.name}: {exc}") from exc
        if df is None or df.empty:
            return ()
        out: list[RawJob] = []
        for record in df.to_dict(orient="records"):
            raw = _row_to_raw(self.name, _drop_missing(record))
            if raw.title and raw.source_url:
                out.append(raw)
        return out


def _row_to_raw(source: str, record: dict) -> RawJob:
    description = _clean(record.get("description")) or ""
    skills_blob = _clean(record.get("skills")) or ""
    skills = tuple(
        sorted({s.strip() for s in skills_blob.split(",") if s.strip()})
    )
    return RawJob(
        source=source,
        source_id=_clean(record.get("id")),
        title=_clean(record.get("title")) or "",
        company=_clean(record.get("company")),
        location=_clean(record.get("location")),
        work_arrangement=_work_arrangement(record),
        employment_type=_job_type_from_record(record),
        seniority=_clean(record.get("job_level")),
        description=description,
        required_skills=skills,
        source_url=_clean(record.get("job_url")) or "",
        # ``company_url`` is the employer's page on the board (e.g. a
        # LinkedIn company profile), never a link to this vacancy.
        official_url=_clean(record.get("company_url")),
        apply_url=_clean(record.get("job_url_direct")),
        date_posted=_parse_date(record.get("date_posted")),
    )


def _drop_missing(record: dict) -> dict:
    """Replace pandas' missing markers (NaN, NaT, None) with ``None``.

    A DataFrame column with gaps hands back ``float('nan')`` — a truthy
    float — where the board sent nothing, which breaks every string
    operation downstream.
    """
    out = {}
    for key, value in record.items():
        try:
            missing = value is None or bool(value != value)
        except (TypeError, ValueError):
            missing = False
        out[key] = None if missing else value
    return out


def _clean(value: object) -> str | None:
    """A stripped non-empty string, or ``None``. Non-strings (ids) are stringified."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and value != value:
        return None
    text = value.strip() if isinstance(value, str) else str(value).strip()
    return text or None


def _work_arrangement(record: dict) -> WorkArrangement:
    if record.get("is_remote") is True:
        return WorkArrangement.REMOTE
    wfh = (_clean(record.get("work_from_home_type")) or "").lower()
    if wfh == "remote":
        return WorkArrangement.REMOTE
    if wfh == "hybrid":
        return WorkArrangement.HYBRID
    return WorkArrangement.UNKNOWN


def _job_type_from_record(record: dict) -> EmploymentType:
    value = (_clean(record.get("job_type")) or "").lower()
    if "full" in value:
        return EmploymentType.FULL_TIME
    if "part" in value:
        return EmploymentType.PART_TIME
    if "intern" in value:
        return EmploymentType.INTERNSHIP
    if "contract" in value:
        return EmploymentType.CONTRACT
    if "temporary" in value:
        return EmploymentType.TEMPORARY
    return EmploymentType.UNKNOWN


def _parse_date(value: object) -> datetime | None:
    from jobsearch.textnorm import parse_iso_datetime  # local import to avoid cycle

    if value is None or (isinstance(value, float) and value != value):
        return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    parsed = parse_iso_datetime(value)
    if parsed is None and value:
        # Pandas Timestamps are not iso strings. Convert via isoformat.
        try:
            parsed = parse_iso_datetime(value.isoformat())
        except AttributeError:
            parsed = None
    return parsed


def build_source(site_token: str) -> SourceAdapter:
    """Convenience factory used by the CLI to spin up a JobSpy-backed source."""
    return JobSpySource(site_token)