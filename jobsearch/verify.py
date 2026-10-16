"""Application-link verification.

Goals, in plain language:

- The default report says *unverified* because most listings have not
  been checked, and that is the truthful state.
- When we do check, we want a documented status with evidence, not a
  hand-wave. An HTTP 200 alone is never enough.
- Job boards that block automated requests (LinkedIn, Indeed, …) are
  never fetched: a 403 or a login page from them says nothing about the
  vacancy, so they are reported as ``uncertain`` with a reason.
- We never request URLs that resolve to private or loopback addresses
  (SSRF guard). Redirects are followed one hop at a time and every hop
  is checked *before* it is requested.

Anything more elaborate (headless browsers, captcha solving) is out of
scope: the pipeline never bypasses anti-bot systems.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlsplit

import requests

from jobsearch.errors import VerificationError
from jobsearch.models import LinkStatus
from jobsearch.normalize import canonicalize_url, is_public_url

DEFAULT_TIMEOUT = 8  # seconds
MAX_REDIRECTS = 5
_USER_AGENT = "JobSearchPipeline/1.0 (+link-verify)"


@dataclass(frozen=True)
class VerifyConfig:
    """Verification knobs. The CLI builds this from the profile's ``[verify]``."""

    timeout: float = DEFAULT_TIMEOUT
    uncertain_hosts: tuple[str, ...] = ()
    login_markers: tuple[str, ...] = ()
    expired_phrases: tuple[str, ...] = ()
    workers: int = 8


def _default_config() -> VerifyConfig:
    from jobsearch.profile import (
        DEFAULT_VERIFY_EXPIRED_PHRASES,
        DEFAULT_VERIFY_LOGIN_MARKERS,
        DEFAULT_VERIFY_UNCERTAIN_HOSTS,
    )

    return VerifyConfig(
        uncertain_hosts=DEFAULT_VERIFY_UNCERTAIN_HOSTS,
        login_markers=DEFAULT_VERIFY_LOGIN_MARKERS,
        expired_phrases=DEFAULT_VERIFY_EXPIRED_PHRASES,
    )


@dataclass
class VerificationResult:
    status: LinkStatus
    evidence: str
    final_url: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _host_matches(url: str, hosts: Iterable[str]) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    for candidate in hosts:
        candidate = candidate.lower().lstrip(".")
        if host == candidate or host.endswith("." + candidate):
            return candidate
    return None


def _login_marker(url: str, markers: Iterable[str]) -> str | None:
    parts = urlsplit(url)
    target = f"{parts.hostname or ''}{parts.path}".lower()
    for marker in markers:
        if marker.lower() in target:
            return marker
    return None


def verify_url(
    url: str,
    *,
    timeout: float | None = None,
    config: VerifyConfig | None = None,
) -> VerificationResult:
    """Check a single URL.

    Returns a :class:`VerificationResult` describing what we observed.
    Never raises for transient failures — those become ``unreachable``.
    Only raises :class:`VerificationError` for programmatic misuse (empty
    URL).
    """
    if not url:
        raise VerificationError("verify_url: empty URL")
    cfg = config or _default_config()
    wait = timeout if timeout is not None else cfg.timeout

    blocked = _host_matches(url, cfg.uncertain_hosts)
    if blocked:
        return VerificationResult(
            status=LinkStatus.UNCERTAIN,
            evidence=f"{blocked} blocks automated checks; open it to confirm",
            final_url=url,
        )

    current = url
    response = None
    for _ in range(MAX_REDIRECTS + 1):
        if not is_public_url(current):
            return VerificationResult(
                status=LinkStatus.UNREACHABLE,
                evidence=(
                    "URL did not resolve to a public address"
                    if current == url
                    else f"redirect to non-public address refused: {current}"
                ),
                final_url=url,
            )
        try:
            response = requests.get(
                current,
                timeout=wait,
                allow_redirects=False,
                headers={"User-Agent": _USER_AGENT},
            )
        except requests.RequestException as exc:
            return VerificationResult(
                status=LinkStatus.UNREACHABLE,
                evidence=f"network error: {exc}",
                final_url=current,
            )
        location = response.headers.get("Location")
        if response.is_redirect and location:
            current = urljoin(current, location)
            continue
        break
    else:
        return VerificationResult(
            status=LinkStatus.UNCERTAIN,
            evidence=f"more than {MAX_REDIRECTS} redirects",
            final_url=current,
        )

    final_url = current
    blocked = _host_matches(final_url, cfg.uncertain_hosts)
    marker = _login_marker(final_url, cfg.login_markers) if final_url != url else None
    if blocked or marker:
        return VerificationResult(
            status=LinkStatus.UNCERTAIN,
            evidence=f"redirected to a sign-in or blocked page: {final_url}",
            final_url=final_url,
        )

    if response.status_code in (401, 403, 429):
        return VerificationResult(
            status=LinkStatus.UNCERTAIN,
            evidence=f"HTTP {response.status_code} (access refused, not proof the job is gone)",
            final_url=final_url,
        )
    if response.status_code >= 400:
        return VerificationResult(
            status=LinkStatus.UNREACHABLE,
            evidence=f"HTTP {response.status_code}",
            final_url=final_url,
        )

    body = response.text.lower()
    if any(phrase.lower() in body for phrase in cfg.expired_phrases):
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


def verify_many(
    urls: list[str], config: VerifyConfig | None = None
) -> list[VerificationResult | None]:
    """Verify ``urls`` concurrently; ``None`` where a check crashed."""
    cfg = config or _default_config()

    def _one(target: str) -> VerificationResult | None:
        try:
            return verify_url(target, config=cfg)
        except Exception:  # noqa: BLE001 - verification is best-effort
            return None

    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=max(1, cfg.workers)) as pool:
        return list(pool.map(_one, urls))
