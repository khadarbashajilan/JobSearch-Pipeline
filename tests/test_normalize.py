"""Tests for URL normalization and SSRF guards."""

from __future__ import annotations

from jobsearch.normalize import canonicalize_url, is_public_url


def test_canonicalize_drops_tracking_params() -> None:
    cleaned = canonicalize_url(
        "https://Example.com/Job?id=42&utm_source=x&gclid=zz&name=Engineer"
    )
    assert "example.com" in cleaned
    assert "utm_source" not in cleaned
    assert "gclid" not in cleaned
    assert "id=42" in cleaned
    assert "name=engineer" in cleaned


def test_canonicalize_strips_default_port_and_sorts() -> None:
    a = canonicalize_url("https://example.com:443/jobs?b=2&a=1")
    b = canonicalize_url("https://example.com/jobs?a=1&b=2")
    assert a == b


def test_canonicalize_preserves_meaningful_query() -> None:
    cleaned = canonicalize_url("https://example.com/jobs?gh_jid=1234")
    assert "gh_jid=1234" in cleaned


def test_is_public_url_accepts_public_address() -> None:
    assert is_public_url("https://example.com/job/1") is True


def test_is_public_url_rejects_loopback() -> None:
    assert is_public_url("http://127.0.0.1/x") is False
    assert is_public_url("http://localhost/x") is False


def test_is_public_url_rejects_private_ip() -> None:
    assert is_public_url("http://10.0.0.5/x") is False
    assert is_public_url("http://192.168.1.1/x") is False


def test_is_public_url_rejects_metadata_ip() -> None:
    assert is_public_url("http://169.254.169.254/latest/meta-data/") is False


def test_is_public_url_rejects_non_http() -> None:
    assert is_public_url("ftp://example.com/file") is False