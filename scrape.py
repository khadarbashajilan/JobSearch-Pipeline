#!/usr/bin/env python3
"""
JobSearch Automation Pipeline — scrape.py

This script reads a machine-readable job search profile from resume.md,
derives the correct query for each job board, scrapes multiple boards
concurrently, filters results locally for uniform behavior, and writes
a clean markdown report.

Why this exists:
- Job board APIs behave inconsistently (LinkedIn ignores -X exclusions,
  Indeed over-narrows with -X exclusions). Filtering locally gives uniform
  behavior across all sites.

Pipeline:
1. Load profile from resume.md (extract TOML under "## Job Search Profile")
2. Validate profile (titles.primary, keywords.core etc.)
3. Build per-site queries (boolean for Indeed, plain keywords for others)
4. Scrape grouped by query form (with retries and backoff)
5. Post-filter locally (date window + title exclusions) for uniformity
6. Render markdown report grouped by site and write to saved/job-links-*.md
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    sys.exit(
        "error: reading the profile needs Python 3.11+ (tomllib).\n"
        f"       running {sys.version.split()[0]}. Recreate .venv with a newer Python."
    )

from jobspy import scrape_jobs
from jobspy.model import Country, Site

PROFILE_HEADER = r"^##\s+Job Search Profile\s*$"
TOML_FENCE = re.compile(r"```toml\s*\n(.*?)```", re.S)
NEXT_HEADING = re.compile(r"^##\s+\S", re.M)
MAX_CORE_KEYWORDS = 6
RETRY_BACKOFF = 12

BOOL_SITES = {Site.INDEED}


class ProfileError(Exception):
    pass


def load_profile(resume_path: Path) -> dict:
    if not resume_path.is_file():
        raise ProfileError(f"resume not found: {resume_path}")

    text = resume_path.read_text(encoding="utf-8")

    # Find the "## Job Search Profile" header (must be a top-level heading).
    header = re.search(PROFILE_HEADER, text, re.M)
    if not header:
        raise ProfileError(
            f"{resume_path} has no '## Job Search Profile' section.\n"
            "       Add a '## Job Search Profile' section with a fenced toml block to resume.md."
        )

    # Cut the section at the next "## " heading, so a second fenced toml block
    # further down the resume can never be picked up by accident.
    body = text[header.end() :]
    nxt = NEXT_HEADING.search(body)
    section = body[: nxt.start()] if nxt else body

    # The first ```toml fence inside that section is the profile contract.
    fence = TOML_FENCE.search(section)
    if not fence:
        raise ProfileError(
            f"{resume_path}: '## Job Search Profile' contains no ```toml block."
        )

    try:
        return tomllib.loads(fence.group(1))
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(
            f"{resume_path}: malformed toml in profile block\n       {exc}"
        ) from exc


def _get(profile: dict, path: str, kind: type | tuple[type, ...], expected: str):
    """Fetch a dotted key path out of the profile and type-check it.

    Raises ProfileError with the full dotted path (e.g. "search.location") so a
    bad edit in resume.md produces an actionable message instead of a TypeError
    further down the pipeline.
    """
    node: object = profile
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ProfileError(f"{path}: required key is missing")
        node = node[part]
    if not isinstance(node, kind):
        raise ProfileError(f"{path}: expected {expected}, got {type(node).__name__}")
    return node


def _warn_unknown(profile: dict, known: set[str]) -> list[str]:
    return [f"ignoring unknown profile key: {k}" for k in profile if k not in known]


def validate(profile: dict) -> list[str]:
    known = {"titles", "search", "keywords", "queries"}
    warnings = _warn_unknown(profile, known)

    primary = _get(profile, "titles.primary", str, "a string")
    if not primary.strip():
        raise ProfileError("titles.primary: must not be empty")

    _get(profile, "search.location", str, "a string")

    core = _get(profile, "keywords.core", list, "a list of strings")
    if len(core) < 2:
        raise ProfileError(f"keywords.core: need at least 2 entries, got {len(core)}")
    if not all(isinstance(k, str) and k.strip() for k in core):
        raise ProfileError("keywords.core: every entry must be a non-empty string")

    exclude = profile.get("keywords", {}).get("exclude", [])
    if not isinstance(exclude, list) or not all(
        isinstance(e, str) and e.strip() for e in exclude
    ):
        raise ProfileError("keywords.exclude: must be a list of non-empty strings")

    if len(core) > MAX_CORE_KEYWORDS:
        warnings.append(
            f"keywords.core: {len(core)} entries, only the first "
            f"{MAX_CORE_KEYWORDS} are used"
        )

    # country is passed straight to Indeed/Glassdoor, which reject unknown
    # spellings, so check it against the library's enum up front.
    country = profile.get("search", {}).get("country")
    if country is not None:
        if not isinstance(country, str):
            raise ProfileError("search.country: expected a string")
        try:
            Country.from_string(country)
        except ValueError as exc:
            raise ProfileError(f"search.country: {exc}") from exc

    return warnings


def build_queries(profile: dict) -> dict[str, str]:
    """Derive one query per site capability from the profile.

    Two forms come out of this:

    "indeed"  — boolean, e.g. '"GenAI Developer" (Python OR FastAPI) -Senior'.
                Indeed's search engine understands boolean operators; the quoted
                phrase pins the title and the OR group widens on skills.
    "plain"   — flat keywords, e.g. "GenAI Developer Python FastAPI ...".
                Every other board treats its search box as plain text, so sending
                quotes or OR groups there just narrows the pool for no gain.

    queries.indeed in the profile overrides the derived Indeed query. That is the
    knob to turn when Indeed returns too few rows.
    """
    primary = profile["titles"]["primary"].strip()
    core = [k.strip() for k in profile["keywords"]["core"]][:MAX_CORE_KEYWORDS]
    exclude = [e.strip() for e in profile.get("keywords", {}).get("exclude", [])]

    # Quoted title + OR-joined skills, then any -X excludes appended.
    boolean = '"{}" ({})'.format(primary, " OR ".join(core))
    if exclude:
        boolean += " " + " ".join(f"-{e}" for e in exclude)

    # A profile-level override wins over the derived form, so the user can widen
    # the Indeed query without touching keyword ordering.
    override = (profile.get("queries", {}) or {}).get("indeed")
    return {
        "indeed": (
            override.strip()
            if isinstance(override, str) and override.strip()
            else boolean
        ),
        "plain": " ".join([primary, *core]),
    }


def parse_sites(raw: str) -> list[Site]:
    """Parse the --site CLI value ("indeed,linkedin") into Site enum members."""
    sites: list[Site] = []
    for token in (t.strip() for t in raw.split(",")):
        if not token:
            continue
        try:
            sites.append(Site[token.upper()])
        except KeyError:
            valid = ", ".join(s.value for s in Site)
            raise ProfileError(
                f"unknown site '{token}'. valid sites: {valid}"
            ) from None
    if not sites:
        raise ProfileError("--site: no sites given")
    return sites


def resolve(args: argparse.Namespace, profile: dict) -> dict:
    """Merge CLI overrides over profile values into one flat config dict.

    Precedence: explicit CLI flag > profile in resume.md > hardcoded default.
    That way the profile is the normal path and the flags are for one-off runs
    ("just try zip_recruiter today") without editing the file.
    """
    search = profile.get("search", {})
    return {
        "sites": parse_sites(args.site),
        "location": args.location or search.get("location"),
        "country": args.country or search.get("country") or "USA",
        "hours_old": (
            args.hours_old if args.hours_old is not None else search.get("hours_old")
        ),
        "results": args.results or search.get("results_per_site") or 10,
        "indeed_date_filter": bool(search.get("indeed_date_filter", False)),
        "exclude": [
            e.strip()
            for e in profile.get("keywords", {}).get("exclude", [])
            if isinstance(e, str) and e.strip()
        ],
    }


def md_escape(value: object) -> str:
    """Escape a value for safe use inside a markdown line.

    Job titles and company names come from scraped HTML, so they routinely
    contain [ and ] which would otherwise be read as a link by any markdown
    renderer. Backslashes are escaped first so the added backslashes are not
    themselves re-escaped.
    """
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("\r", " ")
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def cell(value: object) -> str:
    """Render one dataframe cell as markdown-safe text.

    Missing values come back as None or NaN depending on the column dtype, and
    str(NaN) is the literal "nan", so both are normalized to an empty string
    here rather than leaking "nan" into the report.
    """
    if value is None:
        return ""
    try:
        if value != value:
            return ""
    except TypeError:
        pass
    return md_escape(value).strip()


def post_filter(df, hours_old: int | None, exclude: list[str]):
    """Apply the date window and exclude words to every site's results.

    A deliberate uniformity choice, not a patch for a broken board API. Both boards
    handle server-side filtering inconsistently: LinkedIn ignores -X outright, and
    Indeed's -X works but over-narrows a thin market (five excludes took 8 rows to 1
    for Bengaluru at 72h). date_posted and title come back reliably from every site,
    so filtering once, locally, is the only approach that behaves the same everywhere.
    """
    import pandas as pd

    if df.empty:
        return df, {"dated_out": 0, "excluded_out": 0}

    out = df
    stats = {"dated_out": 0, "excluded_out": 0}

    if hours_old:
        cutoff = datetime.now() - timedelta(hours=int(hours_old))
        posted = pd.to_datetime(out["date_posted"], errors="coerce")
        keep = posted.isna() | (posted >= cutoff)
        stats["dated_out"] = int((~keep).sum())
        out = out[keep]

    if exclude and not out.empty:
        terms = [e.lower() for e in exclude if e]
        titles = out["title"].astype(str).str.lower()
        keep = ~titles.apply(lambda t: any(term in t for term in terms))
        stats["excluded_out"] = int((~keep).sum())
        out = out[keep]

    return out.reset_index(drop=True), stats


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
    import time

    import pandas as pd

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


def render(df, cfg: dict, queries_by_site: dict[str, str], stats: dict) -> str:
    """Build the markdown report.

    The report records the exact query used per site alongside the links, so a
    thin result set can be diagnosed later by reading which query produced it —
    the file doubles as a record of what was searched.
    """
    stamp = datetime.now()
    header = f"**Location:** {cfg['location']} · **Country:** {cfg['country']}"
    if cfg["hours_old"]:
        header += f" · **Freshness:** last {cfg['hours_old']}h"

    lines = [
        f"# Job Links — {stamp:%Y-%m-%d %H:%M}",
        "",
        header,
        "",
        f"**Sites:** {', '.join(s.value for s in cfg['sites'])}",
        "",
    ]

    counts = site_counts(df, cfg)
    empty = [s for s, n in counts.items() if n == 0]
    lines.append(
        "**"
        + " · ".join(f"{s}: {n}" for s, n in counts.items())
        + f"** — {len(df)} unique jobs"
    )
    lines.append("")

    applied = []
    if cfg["hours_old"]:
        applied.append(f"last {cfg['hours_old']}h")
    if cfg["exclude"]:
        applied.append("dropped " + "/".join(cfg["exclude"]) + " from titles")
    if applied:
        lines += [f"Filtered locally: {' · '.join(applied)}.", ""]

    if empty:
        lines += [
            f"> No results from: {', '.join(empty)}. Job boards throttle and block "
            "aggressively, so an empty site is often transient — rerun, or check "
            "`-v 2` for the board's own error.",
            "",
        ]

    lines += ["## Queries", ""]
    for site in cfg["sites"]:
        lines.append(f"- `{site.value}` → `{queries_by_site[site.value]}`")
    lines.append("")
    if Site.INDEED in cfg["sites"]:
        lines += [
            "> The date window and exclude words above are applied **locally**, to "
            "every site. LinkedIn ignores `-X` terms outright, and on Indeed each "
            "exclusion sharply narrows an already thin pool — so job titles are "
            "filtered here instead. If Indeed looks thin, widen `queries.indeed` in "
            "resume.md; that is the knob that opens the pool up.",
            "",
        ]

    if df.empty:
        lines += [
            "## No results",
            "",
            "Every requested site returned nothing. Common causes, in order of "
            "likelihood: the query is too narrow for this location, Indeed throttled "
            "you, or the board is blocking this IP (Naukri 406, ZipRecruiter/Bayt 403, "
            "Glassdoor 400, BDJobs empty).",
        ]
        return "\n".join(lines) + "\n"

    for site in sorted(counts):
        if counts[site] == 0:
            continue
        group = df[df["site"] == site]
        lines.append(f"## {site} ({counts[site]})")
        lines.append("")
        for _, row in group.iterrows():
            title = cell(row.get("title")) or "untitled"
            url = cell(row.get("job_url"))
            lines.append(f"- [{title}]({url})")
            bits = [
                b for b in (cell(row.get("company")), cell(row.get("location"))) if b
            ]
            posted = cell(row.get("date_posted"))
            if bits:
                lines.append(
                    f"  {' · '.join(bits)}" + (f" · {posted}" if posted else "")
                )
            elif posted:
                lines.append(f"  {posted}")
            lines.append("")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    """CLI entry point: parse flags, load the profile, scrape, write the report.

    Everything is validated before any network traffic happens, so a malformed
    profile fails immediately and costs nothing.
    """
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


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        raise SystemExit(130)
