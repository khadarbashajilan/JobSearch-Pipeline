"""Google search / dork URL builders shared by the report and per-job export.

Keeping these in one place means the search report (``report.py``) and the
per-job markdown (``export.py``) render identical links and never drift.
"""

from __future__ import annotations

from urllib.parse import quote, urlparse

_SEARCH_BASE = "https://www.google.com/search?q="


def _domain(url: str | None) -> str:
    """Extract the bare host (no scheme, no ``www.``) from a URL."""
    if not url:
        return ""
    host = urlparse(url).netloc
    return host[4:] if host.startswith("www.") else host


def plain_query(job) -> str:
    """Copy-paste friendly query: company + role + careers."""
    return " ".join(p for p in (job.company, job.title, "careers") if p)


def dork_query(job) -> str:
    """A Google dork aimed at the company's careers page.

    Prefers a ``site:`` dork when a domain can be derived from the job's
    URLs; otherwise falls back to quoted exact-phrase terms.
    """
    title = f'"{job.title}"' if job.title else ""
    company = f'"{job.company}"' if job.company else ""
    domain = _domain(job.apply_url) or _domain(job.official_url) or _domain(job.source_url)
    if domain:
        return " ".join(p for p in (f"site:{domain}", "careers", title) if p)
    return " ".join(p for p in (company, "careers", title) if p)


def google_search_url(job) -> str:
    return _SEARCH_BASE + quote(plain_query(job))


def google_dork_url(job) -> str:
    return _SEARCH_BASE + quote(dork_query(job))
