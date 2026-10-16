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
    return " ".join(str(p) for p in (job.company, job.title, "careers") if isinstance(p, str) and p)


def _is_board(domain: str) -> bool:
    """True for job-board / ATS hosts, which say nothing about the employer."""
    from jobsearch.profile import KNOWN_SITES

    compact = domain.replace("-", "")
    return any(site.replace("_", "") in compact for site in KNOWN_SITES)


def employer_domain(job) -> str:
    """The employer's own domain, if any of the job's URLs is on it."""
    for url in (job.apply_url, job.official_url):
        domain = _domain(url)
        if domain and not _is_board(domain):
            return domain
    return ""


def dork_query(job) -> str:
    """A Google dork aimed at the company's careers page.

    Uses a ``site:`` dork only when the employer's own domain is known — a
    ``site:linkedin.com`` dork searches all of LinkedIn, not the company.
    Otherwise falls back to quoted exact-phrase terms.
    """
    title = f'"{job.title}"' if job.title else ""
    company = f'"{job.company}"' if job.company else ""
    domain = employer_domain(job)
    if domain:
        return " ".join(p for p in (f"site:{domain}", "careers", title) if p)
    return " ".join(p for p in (company, "careers", title) if p)


def google_search_url(job) -> str:
    return _SEARCH_BASE + quote(plain_query(job))


def google_dork_url(job) -> str:
    return _SEARCH_BASE + quote(dork_query(job))
