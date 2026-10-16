"""Tests for ATS source adapters using mocked HTTP responses."""

from __future__ import annotations

import json

import pytest
import responses

from jobsearch.sources import build_ats_source
from jobsearch.sources.base import SourceRequest


def _request(query: str = "python") -> SourceRequest:
    return SourceRequest(
        query=query,
        queries=(query,),
        location="Remote",
        country="USA",
        hours_old=None,
        results_wanted=10,
    )


@responses.activate
def test_greenhouse_parses_listing_payload() -> None:
    payload = {
        "jobs": [
            {
                "id": 100,
                "title": "Senior Engineer",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/100",
                "updated_at": "2024-09-01T12:00:00Z",
                "location": {"name": "Remote"},
                "departments": [{"name": "Engineering"}],
                "offices": [{"name": "Remote"}],
            }
        ]
    }
    responses.add(
        responses.GET,
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
        json=payload,
        status=200,
    )
    source = build_ats_source("greenhouse", "acme")
    rows = list(source.fetch(_request()))
    assert len(rows) == 1
    assert rows[0].title == "Senior Engineer"
    assert rows[0].source_id == "100"
    assert rows[0].apply_url.endswith("/jobs/100")


@responses.activate
def test_lever_parses_postings_payload() -> None:
    payload = [
        {
            "id": "abc",
            "text": "Engineer",
            "categories": {
                "department": "Engineering",
                "location": "Remote",
                "commitment": "Full time",
            },
            "hostedUrl": "https://jobs.lever.co/acme/abc",
            "createdAt": "2024-09-01T12:00:00Z",
        }
    ]
    responses.add(
        responses.GET,
        "https://api.lever.co/v0/postings/acme",
        json=payload,
        status=200,
    )
    source = build_ats_source("lever", "acme")
    rows = list(source.fetch(_request()))
    assert len(rows) == 1
    assert rows[0].title == "Engineer"
    assert rows[0].apply_url.endswith("/abc")


@responses.activate
def test_ashby_parses_board_payload() -> None:
    payload = {
        "jobs": [
            {
                "id": "ashby-1",
                "title": "Engineer",
                "location": "Remote",
                "department": "Engineering",
                "employmentType": "FullTime",
                "jobUrl": "https://jobs.ashbyhq.com/acme/ashby-1",
            }
        ]
    }
    responses.add(
        responses.GET,
        "https://api.ashbyhq.com/posting-api/job-board/acme",
        json=payload,
        status=200,
    )
    source = build_ats_source("ashby", "acme")
    rows = list(source.fetch(_request()))
    assert len(rows) == 1
    assert rows[0].apply_url.endswith("/ashby-1")


@responses.activate
def test_greenhouse_empty_response_yields_no_rows() -> None:
    responses.add(
        responses.GET,
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
        json={"jobs": []},
        status=200,
    )
    source = build_ats_source("greenhouse", "acme")
    assert list(source.fetch(_request())) == []


@responses.activate
def test_ats_source_handles_http_error() -> None:
    from jobsearch.errors import SourceError

    responses.add(
        responses.GET,
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
        status=500,
    )
    source = build_ats_source("greenhouse", "acme")
    with pytest.raises(SourceError):
        list(source.fetch(_request()))


@responses.activate
def test_ats_source_handles_malformed_json() -> None:
    from jobsearch.errors import SourceError

    responses.add(
        responses.GET,
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
        body="not json",
        status=200,
    )
    source = build_ats_source("greenhouse", "acme")
    with pytest.raises(SourceError):
        list(source.fetch(_request()))


def test_ats_factory_rejects_unknown_name() -> None:
    from jobsearch.errors import SourceError

    with pytest.raises(SourceError):
        build_ats_source("unknown_ats", "x")