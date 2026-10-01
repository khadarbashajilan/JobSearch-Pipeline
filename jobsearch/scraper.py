"""Scraping orchestration: query-form grouping, retries, dedup, per-site counts."""

from __future__ import annotations

import sys
import time

import pandas as pd
from jobspy import scrape_jobs
from jobspy.model import Site

from jobsearch.filtering import post_filter

RETRY_BACKOFF = 12

BOOL_SITES = {Site.INDEED}


def scrape_all(cfg: dict, queries: dict[str, str], verbose: int, retries: int = 1):
    """Scrape every requested site, retrying, then filter and dedupe.

    Sites are grouped by query form (boolean vs plain) so all sites sharing a
    form go out in a single scrape_jobs() call — that library fans them out
    concurrently, so one call per form is what makes this fast.

    Retry rationale: job boards throttle hard and return empty frames rather
    than errors, and Indeed is observably flaky (the same query returned 5 rows
    one minute and 0 the next). A single retry with linear backoff absorbs that
    without hammering the board.

    Dedup on job_url afterwards: the same posting is routinely syndicated to
    more than one board.
    """
    frames = []
    groups: dict[str, list[Site]] = {}
    for site in cfg["sites"]:
        form = "indeed" if site in BOOL_SITES else "plain"
        groups.setdefault(form, []).append(site)

    for form, site_list in groups.items():
        # Indeed's own date filter works, but it is redundant with the local one
        # and roughly halves the rows returned in a thin market, so it stays off
        # unless the profile explicitly asks for it.
        skip_date = form == "indeed" and not cfg["indeed_date_filter"]
        for attempt in range(retries + 1):
            df = scrape_jobs(
                site_name=site_list,
                search_term=queries[form],
                location=cfg["location"],
                results_wanted=cfg["results"],
                country_indeed=cfg["country"],
                hours_old=None if skip_date else cfg["hours_old"],
                verbose=verbose,
            )
            if df is not None and not df.empty:
                frames.append(df)
                break
            if attempt < retries:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
                if verbose:
                    print(
                        f"   {form} group returned nothing, retrying",
                        file=sys.stderr,
                    )

    if not frames:
        return pd.DataFrame(), {"dated_out": 0, "excluded_out": 0}
    combined = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["job_url"])
    return post_filter(combined, cfg["hours_old"], cfg["exclude"])


def site_counts(df, cfg: dict) -> dict[str, int]:
    """Count rows per site, always reporting every requested site.

    Sites with zero results are reported explicitly rather than omitted, so a
    throttled or blocked board shows up as a visible 0 instead of silently
    vanishing from the report.
    """
    if df.empty:
        return {site.value: 0 for site in cfg["sites"]}
    counts = {site.value: 0 for site in cfg["sites"]}
    for site, group in df.groupby("site"):
        counts[site] = len(group)
    return counts