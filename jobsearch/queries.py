"""Query and config derivation from the profile.

Turns the profile into one query per site capability, and merges CLI overrides
over profile values into a single flat config dict.
"""

from __future__ import annotations

import argparse

from jobspy.model import Site

from jobsearch.profile import MAX_CORE_KEYWORDS, ProfileError


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