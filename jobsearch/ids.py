"""Stable identifier derivation for jobs.

A job's id is a sha1 of the strongest identity we have for it: source + the
canonicalized source URL. That makes the id reproducible across runs while
remaining unique enough that two distinct postings never collide. When a
source also supplies its own id, we keep it on the record but do not mix it
into the hash.
"""

from __future__ import annotations

import hashlib

from jobsearch.normalize import canonicalize_url


def job_id(source: str, source_id: str | None, url: str) -> str:
    """Return a stable 40-char id for a job.

    ``source_id`` is mixed in too when present so two different ATSes that
    happen to use the same URL scheme for different roles get different ids.
    """
    canonical = canonicalize_url(url)
    seed = f"{source}|{source_id or ''}|{canonical}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()