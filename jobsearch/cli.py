"""CLI entry point: parse flags, load the profile, scrape, write the report.

Everything is validated before any network traffic happens, so a malformed
profile fails immediately and costs nothing.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from jobsearch.profile import ProfileError, load_profile, validate
from jobsearch.queries import build_queries, resolve
from jobsearch.report import render
from jobsearch.scraper import BOOL_SITES, scrape_all


def main() -> int:
    """Parse flags, load the profile, scrape, and write the report."""
    parser = argparse.ArgumentParser(
        prog="run.sh",
        description="Scrape jobs using the profile in resume.md and save links to markdown.",
    )
    parser.add_argument(
        "--site", default="indeed,linkedin", help="comma-separated sites"
    )
    parser.add_argument("--location", help="override profile search.location")
    parser.add_argument("--country", help="override profile search.country")
    parser.add_argument("--results", type=int, help="override results_per_site")
    parser.add_argument("--hours-old", type=int, help="override profile hours_old")
    parser.add_argument("--resume", default="resume.md")
    parser.add_argument("--out-dir", default="saved")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the profile and derived queries, then exit without scraping",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="retry a site group once if it returns nothing (default 1, 0 disables)",
    )
    parser.add_argument("-v", "--verbose", type=int, default=0, choices=[0, 1, 2])
    args = parser.parse_args()

    try:
        profile = load_profile(Path(args.resume))
        warnings = validate(profile)
        cfg = resolve(args, profile)
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    queries = build_queries(profile)

    for message in warnings:
        print(f"warning: {message}", file=sys.stderr)

    queries_by_site = {
        site.value: (queries["indeed"] if site in BOOL_SITES else queries["plain"])
        for site in cfg["sites"]
    }

    if args.dry_run:
        # Prints the profile and the queries it would send, then exits without
        # touching a job board. Cheap, and it is the check to run after editing
        # the toml block in resume.md.
        print(f"profile: {args.resume}")
        print(f"  titles.primary    : {profile['titles']['primary']}")
        print(
            f"  titles.also       : {', '.join(profile['titles'].get('also', [])) or '-'}"
        )
        print(f"  search.location   : {cfg['location']}")
        print(f"  search.country    : {cfg['country']}")
        print(f"  results_per_site  : {cfg['results']}")
        print(f"  search.hours_old  : {cfg['hours_old']} (applied locally)")
        print(f"  keywords.core     : {', '.join(profile['keywords']['core'])}")
        print(f"  keywords.exclude  : {', '.join(cfg['exclude']) or '-'}")
        print("\nderived queries:")
        for site, query in queries_by_site.items():
            print(f"  {site:<14} {query}")
        return 0

    df, stats = scrape_all(cfg, queries, args.verbose, args.retries)
    content = render(df, cfg, queries_by_site, stats)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now()
    out_file = out_dir / f"job-links-{stamp:%Y-%m-%d_%H-%M-%S}.md"
    out_file.write_text(content, encoding="utf-8")

    print(f"\n{len(df)} jobs -> {out_file}")
    return 0