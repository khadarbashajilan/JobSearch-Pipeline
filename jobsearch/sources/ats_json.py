"""Generic JSON-based ATS adapters: Greenhouse, Lever, Ashby.

These three ATSes publish public, unauthenticated JSON feeds of their
job boards — they are the most reliable way to add direct, employer-
authoritative job data without scraping. Each adapter:

- Discovers ``board_token`` via either a CLI flag or per-source config.
- Issues a single GET against the public feed.
- Tolerates schema drift: unknown fields are ignored, missing fields
  stay ``None``.

The adapters share the same fetch skeleton; only the URL template and
the row-to-:class:`RawJob` function differ.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable

import requests

from jobsearch.errors import SourceError
from jobsearch.models import EmploymentType, RawJob, WorkArrangement
from jobsearch.sources.base import SourceAdapter, SourceRequest

DEFAULT_TIMEOUT = 15  # seconds


def _get_json(url: str, params: dict | None = None) -> object:
    """Tiny wrapper kept here so every adapter behaves identically."""
    response = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _parse_iso(value: str | None) -> datetime | None:
    from jobsearch.textnorm import parse_iso_datetime  # avoid cycle

    return parse_iso_datetime(value)


class _JsonATSAdapter(SourceAdapter):
    """Skeleton for adapters that pull a JSON feed."""

    url_template: str = ""
    name: str = ""

    def __init__(self, board_token: str):
        if not board_token:
            raise SourceError(f"{self.name}: board_token is required")
        self.board_token = board_token

    def fetch(self, request: SourceRequest) -> Iterable[RawJob]:
        url = self.url_template.format(board=self.board_token)
        try:
            payload = _get_json(url)
        except requests.RequestException as exc:
            raise SourceError(f"{self.name}: network error: {exc}") from exc
        except ValueError as exc:
            raise SourceError(f"{self.name}: malformed JSON: {exc}") from exc
        rows = self._extract_rows(payload)
        return [self._to_raw(row) for row in rows]

    def _extract_rows(self, payload: object) -> list[dict]:
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            jobs = payload.get("jobs")
            if isinstance(jobs, list):
                return [row for row in jobs if isinstance(row, dict)]
        return []

    def _to_raw(self, row: dict) -> RawJob:
        raise NotImplementedError


class GreenhouseSource(_JsonATSAdapter):
    """Public Greenhouse job board feed.

    URL pattern: ``https://boards-api.greenhouse.io/v1/boards/<token>/jobs``
    Schema is documented at https://developers.greenhouse.io/job-board.html
    """

    name = "greenhouse"
    url_template = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"

    def _to_raw(self, row: dict) -> RawJob:
        location = row.get("location") or {}
        loc_str = location.get("name") if isinstance(location, dict) else None
        absolute_url = row.get("absolute_url") or ""
        departments = row.get("departments") or []
        department = departments[0].get("name") if departments else None
        offices = row.get("offices") or []
        office = offices[0].get("name") if offices else None
        if not loc_str:
            loc_str = office or department
        return RawJob(
            source=self.name,
            source_id=str(row.get("id")) if row.get("id") is not None else None,
            title=strip_or_none(row.get("title")),
            company=department,
            location=strip_or_none(loc_str),
            work_arrangement=WorkArrangement.UNKNOWN,
            employment_type=EmploymentType.UNKNOWN,
            description=None,  # not on the listing endpoint
            source_url=absolute_url,
            official_url=absolute_url,
            apply_url=absolute_url,
            date_posted=_parse_iso(row.get("updated_at")),
        )


class LeverSource(_JsonATSAdapter):
    """Public Lever postings feed.

    URL pattern: ``https://api.lever.co/v0/postings/<token>?mode=json``
    Schema: https://hire.lever.co/developer/documentation
    """

    name = "lever"
    url_template = "https://api.lever.co/v0/postings/{board}?mode=json"

    def _to_raw(self, row: dict) -> RawJob:
        categories = row.get("categories") or {}
        department = categories.get("department")
        location = categories.get("location")
        commitment = (categories.get("commitment") or "").lower()
        employment = _lever_employment(commitment)
        url = row.get("hostedUrl") or row.get("applyUrl") or ""
        return RawJob(
            source=self.name,
            source_id=row.get("id"),
            title=strip_or_none(row.get("text")),
            company=department,
            location=strip_or_none(row.get("categories", {}).get("location"))
            or strip_or_none(location),
            work_arrangement=WorkArrangement.UNKNOWN,
            employment_type=employment,
            description=None,
            source_url=url,
            official_url=url,
            apply_url=url,
            date_posted=_parse_iso(row.get("createdAt")),
        )


class AshbySource(_JsonATSAdapter):
    """Public Ashby job board feed.

    URL pattern: ``https://api.ashbyhq.com/posting-api/job-board/<token>``
    Schema: https://developers.ashbyhq.com/posting-api/job-board
    """

    name = "ashby"
    url_template = "https://api.ashbyhq.com/posting-api/job-board/{board}"

    def _extract_rows(self, payload: object) -> list[dict]:
        if isinstance(payload, dict):
            jobs = payload.get("jobs")
            if isinstance(jobs, list):
                return [row for row in jobs if isinstance(row, dict)]
        return []

    def _to_raw(self, row: dict) -> RawJob:
        location = row.get("location") or ""
        location_str = (
            location.strip() if isinstance(location, str) else None
        )
        url = row.get("jobUrl") or row.get("applyUrl") or ""
        department = row.get("department")
        employment_str = (row.get("employmentType") or "").lower()
        return RawJob(
            source=self.name,
            source_id=row.get("id"),
            title=strip_or_none(row.get("title")),
            company=department,
            location=strip_or_none(location_str) if location_str else None,
            work_arrangement=WorkArrangement.UNKNOWN,
            employment_type=_ashby_employment(employment_str),
            description=None,
            source_url=url,
            official_url=url,
            apply_url=url,
            date_posted=None,  # not surfaced on the public board endpoint
        )


def _lever_employment(value: str) -> EmploymentType:
    if "full" in value:
        return EmploymentType.FULL_TIME
    if "part" in value:
        return EmploymentType.PART_TIME
    if "contract" in value:
        return EmploymentType.CONTRACT
    if "intern" in value:
        return EmploymentType.INTERNSHIP
    return EmploymentType.UNKNOWN


def _ashby_employment(value: str) -> EmploymentType:
    mapping = {
        "full_time": EmploymentType.FULL_TIME,
        "fulltime": EmploymentType.FULL_TIME,
        "part_time": EmploymentType.PART_TIME,
        "parttime": EmploymentType.PART_TIME,
        "contract": EmploymentType.CONTRACT,
        "internship": EmploymentType.INTERNSHIP,
    }
    return mapping.get(value, EmploymentType.UNKNOWN)


def strip_or_none(value: object) -> str | None:
    """Return ``value`` stripped if it is a non-empty string, else ``None``."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def build_ats_source(name: str, board_token: str) -> SourceAdapter:
    """Factory used by the CLI based on the source name."""
    table: dict[str, Callable[[str], SourceAdapter]] = {
        "greenhouse": GreenhouseSource,
        "lever": LeverSource,
        "ashby": AshbySource,
    }
    if name not in table:
        raise SourceError(f"unknown ATS source: {name}")
    return table[name](board_token)