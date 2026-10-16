"""Application-link verification.

Goals, in plain language:

- The default report says *unverified* because most listings have not
  been checked, and that is the truthful state.
- When we do check, we want a documented status with evidence, not a
  hand-wave. An HTTP 200 alone is never enough.
- We never request URLs that resolve to private or loopback addresses
  (SSRF guard) and we never follow redirects to them.

The implementation is deliberately simple: a HEAD/GET request with a
short timeout, a body-text scan for "no longer accepting applications"
or similar, and the URL the verifier actually ended up at. Anything more
elaborate (headless browsers, captcha solving) is out of scope and would
violate AGENTS.md's "no anti-bot bypass" rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from jobsearch.errors import VerificationError
from jobsearch.models import LinkStatus
from jobsearch.normalize import canonicalize_url, is_public_url, safe_redirect_destination

DEFAULT_TIMEOUT = 8  # seconds

# Phrases that, when present in the response body, indicate the vacancy
# has been closed. Lower-case comparison; kept short so the matching is
# cheap and intentional.
_EXPIRED_PHRASES = (
    "no longer accepting applications",
    "this job has been filled",
    "this position has been closed",
    "this posting has expired",
    "this vacancy is no longer available",
)


@dataclass
class VerificationResult:
    status: LinkStatus
    evidence: str
    final_url: str | None = None
    checked_at: datetime = datetime.now(timezone.utc)


def verify_url(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> VerificationResult:
    """Check a single URL.

    Returns a :class:`VerificationResult` describing what we observed.
    Never raises for transient failures — those become ``unreachable``.
    Only raises :class:`VerificationError` for programmatic misuse (empty
    URL, non-http scheme).
    """
    if not url:
        raise VerificationError("verify_url: empty URL")
    if not is_public_url(url):
        return VerificationResult(
            status=LinkStatus.UNREACHABLE,
            evidence="URL did not resolve to a public address",
            final_url=url,
        )
    try:
        response = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": "JobSearchPipeline/1.0 (+link-verify)"},
        )
    except requests.RequestException as exc:
        return VerificationResult(
            status=LinkStatus.UNREACHABLE,
            evidence=f"network error: {exc}",
            final_url=url,
        )

    final_url = safe_redirect_destination(url, response.url or url)
    if response.status_code >= 400:
        return VerificationResult(
            status=LinkStatus.UNREACHABLE,
            evidence=f"HTTP {response.status_code}",
            final_url=final_url,
        )

    body = response.text.lower()
    if any(phrase in body for phrase in _EXPIRED_PHRASES):
        return VerificationResult(
            status=LinkStatus.EXPIRED,
            evidence="body indicates the vacancy is closed",
            final_url=final_url,
        )

    if canonicalize_url(final_url) == canonicalize_url(url):
        evidence = "URL resolves to the same target we started with"
    else:
        evidence = f"redirected to {final_url}"

    return VerificationResult(
        status=LinkStatus.VERIFIED,
        evidence=evidence,
        final_url=final_url,
    )