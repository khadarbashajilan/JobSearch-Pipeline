"""CLI entry point — subcommands for the upgraded pipeline.

Existing v1 flags remain supported so old ``run.sh`` invocations keep
working. New commands are subcommands: ``search``, ``list``, ``status``,
``inspect``, ``doctors``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from jobsearch import export, store
from jobsearch.errors import JobSearchError
from jobsearch.models import ApplicationStatus, LinkStatus, StoredJob
from jobsearch.orchestrate import run_pipeline
from jobsearch.profile import load_profile
from jobsearch.queries import build_queries, parse_sites
from jobsearch.report import render as render_report
from jobsearch.verify import verify_url

_KNOWN_COMMANDS = ("list", "status", "inspect", "doctors", "rclone")

_JOBS_DIR = Path("saved/")
_DEFAULT_RCLONE_REMOTE = "gdrive"
_DEFAULT_RCLONE_DEST = "Saved_Jobs"


def _shared_flags() -> argparse.ArgumentParser:
    """Reserved for callers that want the same flag set as a parent.

    Kept around so future subparsers can opt in via ``parents=[...]``
    without duplicating declarations.
    """
    # ``argparse.SUPPRESS`` (rather than a concrete default) lets a shared flag
    # appear *before* a subcommand without the subparser's own default
    # overwriting the value the main parser already parsed. Concrete defaults
    # are applied in :func:`_apply_defaults` after parsing completes.
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--resume", default=argparse.SUPPRESS)
    parent.add_argument("--db", default=argparse.SUPPRESS)
    parent.add_argument("--out-dir", default=argparse.SUPPRESS)
    parent.add_argument("--site", default=argparse.SUPPRESS)
    parent.add_argument("--location", default=argparse.SUPPRESS)
    parent.add_argument("--country", default=argparse.SUPPRESS)
    parent.add_argument("--results", type=int, default=argparse.SUPPRESS)
    parent.add_argument("--hours-old", type=int, default=argparse.SUPPRESS)
    parent.add_argument("--retries", type=int, default=argparse.SUPPRESS)
    parent.add_argument("-v", "--verbose", type=int, default=argparse.SUPPRESS, choices=[0, 1, 2])
    parent.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
    parent.add_argument("--no-verify", action="store_true", default=argparse.SUPPRESS)
    parent.add_argument("--min-score", type=float, default=argparse.SUPPRESS)
    parent.add_argument("--no-persist", action="store_true", default=argparse.SUPPRESS)
    return parent


def _apply_defaults(args: argparse.Namespace) -> None:
    """Fill in the concrete defaults for shared flags using ``SUPPRESS``.

    Kept separate from ``_shared_flags`` so every consumer gets identical
    fallbacks regardless of whether the flag came before or after the
    subcommand.
    """
    args.resume = getattr(args, "resume", "resume.md")
    args.db = getattr(args, "db", None)
    args.out_dir = getattr(args, "out_dir", "saved")
    args.site = getattr(args, "site", None)
    args.location = getattr(args, "location", None)
    args.country = getattr(args, "country", None)
    args.results = getattr(args, "results", None)
    args.hours_old = getattr(args, "hours_old", None)
    args.retries = getattr(args, "retries", 1)
    args.verbose = getattr(args, "verbose", 0)
    args.dry_run = getattr(args, "dry_run", False)
    args.no_verify = getattr(args, "no_verify", False)
    args.min_score = getattr(args, "min_score", None)
    args.no_persist = getattr(args, "no_persist", False)


def build_parser() -> argparse.ArgumentParser:
    shared = _shared_flags()
    parser = argparse.ArgumentParser(
        prog="jobsearch",
        description="Resume-driven job discovery, matching, and tracking.",
        parents=[shared],
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "search",
        help="discover and rank jobs (default)",
        parents=[shared],
    )
    list_parser = subparsers.add_parser("list", help="list stored jobs", parents=[shared])
    list_parser.add_argument("--status", default="new")
    list_parser.add_argument("--limit", type=int, default=50)
    status = subparsers.add_parser(
        "status",
        help="update application status for a job",
        parents=[shared],
    )
    status.add_argument("job_id")
    status.add_argument("application_status")
    status.add_argument("--note", default=None)
    subparsers.add_parser("inspect", help="show parsed profile", parents=[shared])
    subparsers.add_parser("doctors", help="run diagnostics against the configuration", parents=[shared])
    subparsers.add_parser("rclone", help="sync saved/ to the configured rclone remote", parents=[shared])
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["search", "--dry-run"]  # default: dry-run prints the profile
    args = parser.parse_args(argv)
    _apply_defaults(args)
    if args.command in (None, "search"):
        return _cmd_search(args)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "inspect":
        return _cmd_inspect(args)
    if args.command == "doctors":
        return _cmd_doctors(args)
    if args.command == "rclone":
        return _cmd_rclone(args)
    parser.print_help()
    return 1


def _load_profile_or_exit(args: argparse.Namespace):
    try:
        return load_profile(Path(args.resume))
    except JobSearchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)


def _resolve_profile_overrides(args: argparse.Namespace, profile) -> None:
    if args.location:
        profile.location = args.location
    if args.country:
        profile.country = args.country
        profile.jobspy_country = args.country
    if getattr(args, "results", None):
        profile.results_per_site = args.results
    if getattr(args, "hours_old", None):
        profile.hours_old = args.hours_old
    if getattr(args, "min_score", None) is not None:
        profile.min_score = args.min_score


def _cmd_search(args: argparse.Namespace) -> int:
    profile = _load_profile_or_exit(args)
    _resolve_profile_overrides(args, profile)
    sites = parse_sites(args.site or "indeed,linkedin")

    if args.dry_run:
        print(f"profile: {args.resume}")
        print(f"  primary title     : {profile.primary_title}")
        print(f"  alternative titles: {', '.join(profile.alternative_titles) or '-'}")
        print(f"  location          : {profile.location}")
        print(f"  country           : {profile.jobspy_country}")
        print(f"  results per site  : {profile.results_per_site}")
        print(f"  hours old         : {profile.hours_old}")
        print(f"  remote preference : {profile.remote_preference}")
        print(f"  employment types  : {', '.join(profile.employment_types)}")
        print(f"  skills            : {', '.join(profile.skills)}")
        print(f"  excludes          : {', '.join(profile.exclude_keywords) or '-'}")
        print(f"  level filter      : {profile.level_filter}")
        print(f"  min score         : {profile.min_score}")
        print()
        print("derived queries:")
        for label, query in build_queries(profile).items():
            print(f"  {label:<8} {query}")
        print()
        print(f"sites: {', '.join(sites)}")
        return 0

    stored, matches, report = run_pipeline(profile, sites)

    if not args.no_verify:
        _verify_jobs(stored)

    from jobsearch.filtering import filter_jobs

    _, filter_stats = filter_jobs(
        stored,
        hours_old=profile.hours_old,
        exclude_keywords=profile.exclude_keywords,
        level_filter=profile.level_filter,
    )

    db_path = _db_path(args)
    previous_ids = (
        {row.id for row in store.list_jobs(limit=1000, path=db_path)}
        if not args.no_persist
        else set()
    )
    content = render_report(matches, profile, report, filter_stats, previous_ids)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now()
    out_file = out_dir / f"job-links-{stamp:%Y-%m-%d_%H-%M-%S}.md"
    out_file.write_text(content, encoding="utf-8")

    if not args.no_persist:
        run_id = store.start_run(
            location=profile.location,
            query=report.queries.get("plain", ""),
            diagnostics={
                d.source: {"found": d.jobs_found, "error": d.error}
                for d in report.diagnostics
            },
            path=db_path,
        )
        store.upsert_jobs(stored, path=db_path)
        store.record_matches(matches, path=db_path)
        store.finish_run(run_id, path=db_path)

    print(f"\n{len(matches)} matches -> {out_file}")
    return 0


def _verify_jobs(jobs: list) -> None:
    """Best-effort URL verification for the jobs we just discovered."""
    for job in jobs:
        if not isinstance(job, StoredJob):
            continue
        if job.link_status != LinkStatus.UNVERIFIED:
            continue
        url = job.apply_url or job.official_url or job.source_url
        if not url:
            continue
        try:
            result = verify_url(url)
        except Exception:  # noqa: BLE001 - verification is best-effort
            continue
        job.link_status = result.status
        job.link_evidence = result.evidence
        job.link_checked_at = result.checked_at


def _db_path(args: argparse.Namespace) -> Path | None:
    return Path(args.db) if args.db else None


def _cmd_list(args: argparse.Namespace) -> int:
    statuses = _parse_statuses(args.status)
    rows = store.list_jobs(statuses=statuses, limit=args.limit, path=_db_path(args))
    if not rows:
        print("no jobs match")
        return 0
    for row in rows:
        title = row.title or "untitled"
        company = row.company or "?"
        url = row.apply_url or row.official_url or row.source_url
        marker = f"[{row.application_status.value}]"
        print(f"{marker} {title} @ {company}")
        if url:
            print(f"   {url}")
    return 0


def _parse_statuses(raw: str) -> tuple[ApplicationStatus, ...]:
    tokens = [t.strip().lower() for t in raw.split(",") if t.strip()]
    if not tokens:
        return ()
    out = []
    for token in tokens:
        try:
            out.append(ApplicationStatus(token))
        except ValueError:
            valid = ", ".join(s.value for s in ApplicationStatus)
            print(f"error: unknown status '{token}'. valid: {valid}", file=sys.stderr)
            raise SystemExit(1)
    return tuple(out)


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        status = ApplicationStatus(args.application_status.lower())
    except ValueError:
        valid = ", ".join(s.value for s in ApplicationStatus)
        print(f"error: unknown status '{args.application_status}'. valid: {valid}", file=sys.stderr)
        return 1
    try:
        store.update_status(args.job_id, status, note=args.note, path=_db_path(args))
    except JobSearchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"updated {args.job_id} -> {status.value}")

    # Best-effort: also write a per-job markdown under saved/. The
    # SQLite write is the source of truth; if the markdown export fails the
    # status change is still recorded, so we warn and continue.
    db_path = _db_path(args)
    job = next(
        (row for row in store.list_jobs(limit=1000, path=db_path) if row.id == args.job_id),
        None,
    )
    if job is not None:
        try:
            export.write_job_markdown(job, _JOBS_DIR)
        except Exception as exc:  # noqa: BLE001 - export is best-effort
            print(
                f"warning: status updated but could not write {args.job_id}.md: {exc}",
                file=sys.stderr,
            )
    return 0


def _cmd_rclone(args: argparse.Namespace) -> int:
    """Sync ``saved/`` to the configured rclone remote.

    Reads ``JOBSEARCH_RCLONE_REMOTE`` (default ``gdrive``) and
    ``JOBSEARCH_RCLONE_DEST`` (default ``Saved_Jobs``). Uses ``rclone sync``
    with ``--delete-during`` so deletions on the local side propagate to the
    remote. Local files are kept after a successful sync — Drive is a
    one-way mirror, not the source of truth.

    rclone failures propagate as a non-zero exit so transient network or
    auth issues are visible, never silent.
    """
    remote = os.environ.get("JOBSEARCH_RCLONE_REMOTE", _DEFAULT_RCLONE_REMOTE)
    dest = os.environ.get("JOBSEARCH_RCLONE_DEST", _DEFAULT_RCLONE_DEST)
    src = _JOBS_DIR

    if not src.exists() or not any(src.iterdir()):
        print(
            f"nothing to sync: {src} is missing or empty. "
            f"Mark a job as 'saved' first to populate it.",
            file=sys.stderr,
        )
        return 1

    target = f"{remote}:{dest}/"
    print(f"syncing {src}/ -> {target}")
    try:
        subprocess.run(
            ["rclone", "sync", str(src) + "/", target, "-v", "--delete-during"],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(
            f"error: rclone failed (exit {exc.returncode}). "
            f"Run with `rclone sync {src}/ {target} -v --delete-during` to debug.",
            file=sys.stderr,
        )
        return exc.returncode or 1
    except FileNotFoundError:
        print(
            "error: rclone not found on PATH. Install it: "
            "https://rclone.org/install/",
            file=sys.stderr,
        )
        return 127
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    profile = _load_profile_or_exit(args)
    print(json.dumps(
        {
            "primary_title": profile.primary_title,
            "alternative_titles": list(profile.alternative_titles),
            "skills": list(profile.skills),
            "exclude_keywords": list(profile.exclude_keywords),
            "level_filter": profile.level_filter,
            "location": profile.location,
            "country": profile.country,
            "jobspy_country": profile.jobspy_country,
            "remote_preference": profile.remote_preference,
            "employment_types": list(profile.employment_types),
            "hours_old": profile.hours_old,
            "results_per_site": profile.results_per_site,
            "experience_years": profile.experience_years,
            "min_score": profile.min_score,
            "llm_provider": profile.llm_provider,
            "llm_model": profile.llm_model,
            "indeed_date_filter": profile.indeed_date_filter,
        },
        indent=2,
    ))
    return 0


def _cmd_doctors(args: argparse.Namespace) -> int:
    try:
        profile = _load_profile_or_exit(args)
    except SystemExit:
        return 1
    warnings = 0
    if not profile.location:
        print("warning: search.location is empty — sources may behave inconsistently")
        warnings += 1
    if not profile.skills:
        print("warning: keywords.core is empty — matching will be neutral")
        warnings += 1
    if not profile.alternative_titles:
        print("note: titles.also is empty — only the primary title will be searched")
    print(f"doctors: {warnings} warning(s)")
    return 0
