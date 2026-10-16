"""Profile loading, validation, and candidate configuration.

The profile lives in ``resume.md`` under a fenced ``toml`` block named
``## Job Search Profile``. We separate *facts* lifted from the résumé from
*preferences* the candidate edits: this module produces both. The
resulting :class:`CandidateProfile` is the single object every other layer
consumes, so it is the only one that has to know the on-disk format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jobsearch.errors import ProfileError
from jobsearch.resume import (
    ParsedResume,
    extract_section_skills,
    find_skills_heading,
    parse_resume,
)

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    raise SystemExit(
        "error: reading the profile needs Python 3.11+ (tomllib).\n"
        "       Recreate .venv with a newer Python."
    )


PROFILE_HEADER = r"^##\s+Job Search Profile\s*$"
TOML_FENCE = re.compile(r"```toml\s*\n(.*?)```", re.S)
NEXT_HEADING = re.compile(r"^##\s+\S", re.M)
MAX_CORE_KEYWORDS = 6
DEFAULT_HOURS_OLD = 168  # 7 days

# Allowed settings for ``keywords.level_filter``. ``off`` is the default and
# means no level-based cutoff; the others are the maximum level the user is
# willing to consider, evaluated against ``_SENIORITY_RANK`` in matching.
LEVEL_FILTER_VALUES = frozenset({"off", "intern", "junior", "mid", "senior", "lead", "staff", "principal"})
LEVEL_VALUES = LEVEL_FILTER_VALUES - {"off"}

# Every site the CLI knows how to query. ``search.sites`` must be a subset.
KNOWN_SITES = (
    "linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "bayt",
    "naukri", "bdjobs", "greenhouse", "lever", "ashby",
)
DEFAULT_SITES = ("indeed", "linkedin")

# Fallbacks for ``[verify]``. They describe how job boards behave, not who
# the candidate is, and every one can be replaced from the profile.
DEFAULT_VERIFY_UNCERTAIN_HOSTS = (
    "linkedin.com", "indeed.com", "glassdoor.com", "glassdoor.co.in",
    "ziprecruiter.com", "naukri.com",
)
DEFAULT_VERIFY_LOGIN_MARKERS = ("authwall", "login", "signin", "sign-in", "checkpoint", "captcha")
DEFAULT_VERIFY_EXPIRED_PHRASES = (
    "no longer accepting applications",
    "this job has been filled",
    "this position has been closed",
    "this posting has expired",
    "this vacancy is no longer available",
    "this job is no longer available",
    "job not found",
)
REPORT_SEEN_MODES = frozenset({"full", "compact", "hide"})


@dataclass
class CandidateProfile:
    """The single object every layer downstream reads from."""

    primary_title: str
    alternative_titles: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    level_filter: str = "off"  # off | intern | junior | mid | senior | lead | staff | principal
    level_min: str = "off"  # floor: jobs whose inferred level ranks below are dropped
    # true = keep a job only with positive evidence it sits inside the band
    # (a level word, the board's level field, or a years requirement within
    # ``max_required_years``); jobs that state nothing are dropped.
    level_strict: bool = False
    location: str = ""
    # Every location searched; ``location`` is the first one. Sources that
    # ignore location (ATS feeds) run once regardless.
    locations: tuple[str, ...] = ()
    # Local location filter: a job is kept when its location names one of
    # these (whole token). Empty = keep every location.
    allowed_regions: tuple[str, ...] = ()
    keep_remote: bool = True
    keep_unknown_location: bool = True
    country: str = "USA"
    remote_preference: str = "any"  # onsite | remote | hybrid | any
    employment_types: tuple[str, ...] = ("full_time",)
    hours_old: int = DEFAULT_HOURS_OLD
    results_per_site: int = 30
    experience_years: float | None = None
    min_score: float = 0.0
    llm_provider: str | None = None
    llm_model: str | None = None
    jobspy_country: str = "USA"  # Country enum value for JobSpy
    indeed_date_filter: bool = False
    resume: ParsedResume | None = None
    raw_queries: dict[str, str] = field(default_factory=dict)
    # Query terms (``keywords.core``). Kept apart from ``skills`` because the
    # matcher wants the full résumé skill list but queries must stay short.
    core_keywords: tuple[str, ...] = ()
    # Technologies the candidate tracks but does not have. Found in a
    # posting, they count as required-and-missing.
    watch_skills: tuple[str, ...] = ()
    exclude_stack: tuple[str, ...] = ()
    candidate_level: str | None = None
    max_required_years: float | None = None
    level_aliases: dict[str, str] = field(default_factory=dict)
    sites: tuple[str, ...] = DEFAULT_SITES
    retries: int = 1
    retry_backoff_seconds: float = 12.0
    parallel_sources: int = 1
    linkedin_fetch_description: bool = False
    verify_enabled: bool = True
    verify_workers: int = 8
    verify_timeout: float = 8.0
    verify_uncertain_hosts: tuple[str, ...] = DEFAULT_VERIFY_UNCERTAIN_HOSTS
    verify_login_markers: tuple[str, ...] = DEFAULT_VERIFY_LOGIN_MARKERS
    verify_expired_phrases: tuple[str, ...] = DEFAULT_VERIFY_EXPIRED_PHRASES
    report_seen: str = "compact"  # full | compact | hide

    def all_titles(self) -> tuple[str, ...]:
        """Primary plus alternative titles — order-preserving, deduped."""
        seen: set[str] = set()
        ordered: list[str] = []
        for title in (self.primary_title, *self.alternative_titles):
            key = title.strip().lower()
            if title and key not in seen:
                ordered.append(key)
                seen.add(key)
        return tuple(ordered)


def load_profile(resume_path: Path) -> CandidateProfile:
    """Load the TOML block from ``resume_path`` and return a typed profile."""
    if not resume_path.is_file():
        raise ProfileError(f"resume not found: {resume_path}")

    text = resume_path.read_text(encoding="utf-8")
    header = re.search(PROFILE_HEADER, text, re.M)
    if not header:
        raise ProfileError(
            f"{resume_path} has no '## Job Search Profile' section.\n"
            "       Add a '## Job Search Profile' section with a fenced "
            "toml block to resume.md."
        )

    # Bound the section to the next "## " heading so a stray toml block
    # later in the résumé cannot be picked up by accident.
    body = text[header.end() :]
    nxt = NEXT_HEADING.search(body)
    section = body[: nxt.start()] if nxt else body

    fence = TOML_FENCE.search(section)
    if not fence:
        raise ProfileError(
            f"{resume_path}: '## Job Search Profile' contains no ```toml block."
        )

    try:
        data = tomllib.loads(fence.group(1))
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(
            f"{resume_path}: malformed toml in profile block\n       {exc}"
        ) from exc

    profile = _build_profile(data)
    profile.resume = _maybe_parse_resume(resume_path, data)
    profile.skills = _merge_skills(profile.skills, _resume_skills(text, data))
    return profile


def _resume_skills(text: str, data: dict[str, Any]) -> tuple[str, ...]:
    """Skills listed in the résumé prose, per ``skills.resume_section``.

    ``resume_section`` names the ``## `` heading to read. Omitted, the
    first heading containing "skill" is used; ``""`` disables it.
    """
    section = data.get("skills", {}).get("resume_section")
    if section is None:
        section = find_skills_heading(text, exclude=("Job Search Profile",))
    if not section:
        return ()
    return extract_section_skills(text, section)


def _merge_skills(*groups: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for skill in group:
            key = skill.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(skill.strip())
    return tuple(out)


def _maybe_parse_resume(resume_path: Path, data: dict[str, Any]) -> ParsedResume | None:
    """Parse the résumé file, but only if one is configured.

    Failures here are surfaced as warnings, not errors, so a candidate can
    still iterate on preferences even if a malformed résumé slips in.
    """
    if data.get("resume", {}).get("path"):
        try:
            return parse_resume(Path(data["resume"]["path"]))
        except Exception:  # noqa: BLE001 - surfaced to the caller as a warning
            return None
    # Default: parse the file the profile came from. Skip parsing the file
    # we are currently inside of to
    # avoid double-reading; the caller can ask for it explicitly.
    return None


def _build_profile(data: dict[str, Any]) -> CandidateProfile:
    """Construct a :class:`CandidateProfile` from the parsed TOML dict."""
    if not isinstance(data, dict):
        raise ProfileError("profile must be a TOML table at the top level")

    primary = _require_str(data, ("titles", "primary"), "a string")
    if not primary.strip():
        raise ProfileError("titles.primary: must not be empty")

    search_raw = data.get("search") or {}
    locations = _opt_list(search_raw, "locations", ())
    if locations:
        location = locations[0]
    else:
        location = _require_str(data, ("search", "location"), "a string")
        locations = (location,)
    if not all(loc.strip() for loc in locations):
        raise ProfileError("search.locations: entries must be non-empty strings")

    core_keywords = _require_list(data, ("keywords", "core"), "a list of strings")
    if len(core_keywords) < 2:
        raise ProfileError(
            f"keywords.core: need at least 2 entries, got {len(core_keywords)}"
        )
    if not all(isinstance(k, str) and k.strip() for k in core_keywords):
        raise ProfileError("keywords.core: every entry must be a non-empty string")

    exclude = data.get("keywords", {}).get("exclude") or []
    if not isinstance(exclude, list) or not all(
        isinstance(e, str) and e.strip() for e in exclude
    ):
        raise ProfileError("keywords.exclude: must be a list of non-empty strings")

    search = data.get("search") or {}
    country = _opt_str(search, "country", "USA")
    hours_old = _opt_int(search, "hours_old", DEFAULT_HOURS_OLD)
    results_per_site = _opt_int(search, "results_per_site", 30)
    remote = _opt_str(search, "remote", "any")
    if remote not in {"onsite", "remote", "hybrid", "any"}:
        raise ProfileError(
            f"search.remote: expected one of onsite/remote/hybrid/any, got '{remote}'"
        )
    emp_types = _opt_list(search, "employment_types", ("full_time",))
    bad_types = [t for t in emp_types if t not in _VALID_EMPLOYMENT_TYPES]
    if bad_types:
        raise ProfileError(
            f"search.employment_types: unsupported values {bad_types}"
        )

    titles_section = data.get("titles") or {}
    alt_titles = titles_section.get("also") or []
    if not isinstance(alt_titles, list) or not all(
        isinstance(t, str) for t in alt_titles
    ):
        raise ProfileError("titles.also: must be a list of strings")

    experience = data.get("experience") or {}
    experience_years = _opt_number(experience, "years", None)
    max_required_years = _opt_number(experience, "max_required_years", None)
    candidate_level = _opt_str(experience, "level", None)
    if candidate_level is not None and candidate_level not in LEVEL_VALUES:
        raise ProfileError(
            f"experience.level: expected one of {', '.join(sorted(LEVEL_VALUES))}, "
            f"got '{candidate_level}'"
        )

    keywords = data.get("keywords") or {}
    exclude_stack = _opt_list(keywords, "exclude_stack", ())

    skills_section = data.get("skills") or {}
    extra_skills = _opt_list(skills_section, "also", ())
    watch_skills = _opt_list(skills_section, "watch", ())

    levels = data.get("levels") or {}
    level_aliases = levels.get("aliases") or {}
    if not isinstance(level_aliases, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in level_aliases.items()
    ):
        raise ProfileError("levels.aliases: must be a table of \"phrase\" = \"level\"")
    bad_levels = {v for v in level_aliases.values() if v not in LEVEL_VALUES | {"ignore"}}
    if bad_levels:
        raise ProfileError(
            f"levels.aliases: unknown level(s) {sorted(bad_levels)}; "
            f"use one of {', '.join(sorted(LEVEL_VALUES))} or 'ignore'"
        )

    sites = _opt_list(search, "sites", DEFAULT_SITES)
    unknown_sites = [s for s in sites if s not in KNOWN_SITES]
    if unknown_sites or not sites:
        raise ProfileError(
            f"search.sites: unknown or empty {unknown_sites}. Valid: {', '.join(KNOWN_SITES)}"
        )
    retries = _opt_int(search, "retries", 1)
    if retries < 0:
        raise ProfileError("search.retries: must be >= 0")
    retry_backoff = _opt_number(search, "retry_backoff_seconds", 12.0)
    parallel_sources = _opt_int(search, "parallel_sources", 1)
    if parallel_sources < 1:
        raise ProfileError("search.parallel_sources: must be >= 1")

    verify = data.get("verify") or {}
    verify_workers = _opt_int(verify, "workers", 8)
    if verify_workers < 1:
        raise ProfileError("verify.workers: must be >= 1")

    report_section = data.get("report") or {}
    report_seen = _opt_str(report_section, "previously_seen", "compact")
    if report_seen not in REPORT_SEEN_MODES:
        raise ProfileError(
            f"report.previously_seen: expected one of {', '.join(sorted(REPORT_SEEN_MODES))}, "
            f"got '{report_seen}'"
        )
    min_score = _opt_float(data.get("matching") or {}, "min_score", 0.0)
    llm_provider = _opt_str(data.get("llm") or {}, "provider", None)
    llm_model = _opt_str(data.get("llm") or {}, "model", None)

    # ``level_filter`` is the seniority ceiling. ``off`` means no cutoff.
    # When set, jobs whose inferred level ranks above this value are dropped
    # before matching.
    level_filter = _opt_str(data.get("keywords") or {}, "level_filter", "off") or "off"
    if level_filter not in LEVEL_FILTER_VALUES:
        raise ProfileError(
            f"keywords.level_filter: expected one of "
            f"{', '.join(sorted(LEVEL_FILTER_VALUES))}, got '{level_filter}'"
        )
    level_min = _opt_str(data.get("keywords") or {}, "level_min", "off") or "off"
    if level_min not in LEVEL_FILTER_VALUES:
        raise ProfileError(
            f"keywords.level_min: expected one of "
            f"{', '.join(sorted(LEVEL_FILTER_VALUES))}, got '{level_min}'"
        )
    from jobsearch.filtering import SENIORITY_RANK

    if (
        level_min != "off" and level_filter != "off"
        and SENIORITY_RANK[level_min] > SENIORITY_RANK[level_filter]
    ):
        raise ProfileError(
            f"keywords.level_min ('{level_min}') is above keywords.level_filter ('{level_filter}')"
        )

    return CandidateProfile(
        primary_title=primary.strip(),
        alternative_titles=tuple(t.strip() for t in alt_titles if t.strip()),
        skills=_merge_skills(
            tuple(k.strip() for k in core_keywords if k.strip()), extra_skills
        ),
        core_keywords=tuple(k.strip() for k in core_keywords if k.strip()),
        watch_skills=_merge_skills(watch_skills),
        exclude_stack=tuple(e.strip() for e in exclude_stack if e.strip()),
        candidate_level=candidate_level,
        max_required_years=max_required_years,
        level_aliases={k.strip().lower(): v for k, v in level_aliases.items()},
        sites=tuple(sites),
        retries=retries,
        retry_backoff_seconds=float(retry_backoff),
        linkedin_fetch_description=_opt_bool(search, "linkedin_fetch_description", False),
        verify_enabled=_opt_bool(verify, "enabled", True),
        verify_workers=verify_workers,
        verify_timeout=float(_opt_number(verify, "timeout", 8.0)),
        verify_uncertain_hosts=_opt_list(verify, "uncertain_hosts", DEFAULT_VERIFY_UNCERTAIN_HOSTS),
        verify_login_markers=_opt_list(verify, "login_markers", DEFAULT_VERIFY_LOGIN_MARKERS),
        verify_expired_phrases=_opt_list(verify, "expired_phrases", DEFAULT_VERIFY_EXPIRED_PHRASES),
        report_seen=report_seen,
        exclude_keywords=tuple(e.strip() for e in exclude if e.strip()),
        level_filter=level_filter,
        level_min=level_min,
        level_strict=_opt_bool(data.get("keywords") or {}, "level_strict", False),
        location=location,
        locations=tuple(loc.strip() for loc in locations),
        allowed_regions=tuple(r.strip() for r in _opt_list(search, "allowed_regions", ()) if r.strip()),
        keep_remote=_opt_bool(search, "keep_remote", True),
        keep_unknown_location=_opt_bool(search, "keep_unknown_location", True),
        parallel_sources=parallel_sources,
        country=country,
        jobspy_country=_resolve_jobspy_country(country),
        remote_preference=remote,
        employment_types=tuple(emp_types),
        hours_old=hours_old,
        results_per_site=results_per_site,
        experience_years=experience_years,
        min_score=float(min_score),
        llm_provider=llm_provider,
        llm_model=llm_model,
        indeed_date_filter=_opt_bool(search, "indeed_date_filter", False),
        raw_queries={k: str(v) for k, v in (data.get("queries") or {}).items()},
    )


_VALID_EMPLOYMENT_TYPES = {
    "full_time",
    "part_time",
    "contract",
    "temporary",
    "internship",
    "other",
}


def _resolve_jobspy_country(country: str) -> str:
    """Map a friendly country string to a jobspy :class:`Country` value.

    JobSpy's enum accepts a comma-separated list of names per entry — e.g.
    ``"usa,us,united states"``. We pass the first token through unchanged
    when it is, otherwise raise an actionable error.
    """
    from jobspy.model import Country  # local import to keep module cheap

    try:
        enum = Country.from_string(country)
    except ValueError as exc:
        valid = ", ".join(sorted({c.value[0].split(",")[0] for c in Country}))
        raise ProfileError(
            f"search.country: '{country}' is not recognised by JobSpy. "
            f"Valid values include: {valid}"
        ) from exc
    return enum.value[0].split(",")[0]


def _require_str(data: dict[str, Any], path: tuple[str, str], expected: str) -> str:
    node = data
    for part in path:
        if not isinstance(node, dict) or part not in node:
            raise ProfileError(f"{'.'.join(path)}: required key is missing")
        node = node[part]
    if not isinstance(node, str):
        raise ProfileError(
            f"{'.'.join(path)}: expected {expected}, got {type(node).__name__}"
        )
    return node


def _opt_str(section: dict[str, Any], key: str, default: str | None) -> str | None:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ProfileError(
            f"{key}: expected a string, got {type(value).__name__}"
        )
    return value


def _opt_int(section: dict[str, Any], key: str, default: int | None) -> int | None:
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileError(f"{key}: expected an integer, got {type(value).__name__}")
    return value


def _opt_number(section: dict[str, Any], key: str, default: float | None) -> float | None:
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"{key}: expected a number, got {type(value).__name__}")
    if value < 0:
        raise ProfileError(f"{key}: must not be negative")
    return float(value)


def _opt_bool(section: dict[str, Any], key: str, default: bool) -> bool:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ProfileError(f"{key}: expected true or false, got {type(value).__name__}")
    return value


def _opt_float(section: dict[str, Any], key: str, default: float) -> float:
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"{key}: expected a number, got {type(value).__name__}")
    return float(value)


def _require_list(
    data: dict[str, Any], path: tuple[str, str], expected: str
) -> list[Any]:
    node = data
    for part in path:
        if not isinstance(node, dict) or part not in node:
            raise ProfileError(f"{'.'.join(path)}: required key is missing")
        node = node[part]
    if not isinstance(node, list):
        raise ProfileError(
            f"{'.'.join(path)}: expected {expected}, got {type(node).__name__}"
        )
    return node


def _opt_list(
    section: dict[str, Any], key: str, default: tuple[str, ...]
) -> tuple[str, ...]:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ProfileError(f"{key}: expected a list of strings")
    return tuple(value)