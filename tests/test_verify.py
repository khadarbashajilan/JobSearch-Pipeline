"""Tests for application-link verification."""

from __future__ import annotations

import pytest
import responses

from jobsearch.errors import VerificationError
from jobsearch.models import LinkStatus
from jobsearch.verify import verify_url


@responses.activate
def test_verify_success_returns_verified() -> None:
    responses.add(
        responses.GET,
        "https://example.com/jobs/1",
        body="<html>Apply here</html>",
        status=200,
    )
    result = verify_url("https://example.com/jobs/1")
    assert result.status == LinkStatus.VERIFIED
    assert result.evidence


@responses.activate
def test_verify_detects_expired_text() -> None:
    responses.add(
        responses.GET,
        "https://example.com/jobs/1",
        body="<html>This job has been filled.</html>",
        status=200,
    )
    result = verify_url("https://example.com/jobs/1")
    assert result.status == LinkStatus.EXPIRED


@responses.activate
def test_verify_returns_unreachable_for_5xx() -> None:
    responses.add(
        responses.GET,
        "https://example.com/jobs/1",
        status=503,
    )
    result = verify_url("https://example.com/jobs/1")
    assert result.status == LinkStatus.UNREACHABLE


@responses.activate
def test_verify_returns_unreachable_for_connection_error() -> None:
    import requests

    responses.add(
        responses.GET,
        "https://example.com/jobs/1",
        body=requests.ConnectionError("boom"),
    )
    result = verify_url("https://example.com/jobs/1")
    assert result.status == LinkStatus.UNREACHABLE


def test_verify_rejects_loopback_address() -> None:
    result = verify_url("http://127.0.0.1/admin")
    assert result.status == LinkStatus.UNREACHABLE


def test_verify_rejects_private_ip() -> None:
    result = verify_url("http://10.0.0.1/jobs")
    assert result.status == LinkStatus.UNREACHABLE


def test_verify_rejects_metadata_ip() -> None:
    result = verify_url("http://169.254.169.254/latest/meta-data/")
    assert result.status == LinkStatus.UNREACHABLE


def test_verify_empty_url_raises() -> None:
    with pytest.raises(VerificationError):
        verify_url("")