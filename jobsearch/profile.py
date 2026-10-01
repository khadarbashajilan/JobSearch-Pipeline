"""Profile loading and validation.

Extracts the fenced TOML block under "## Job Search Profile" in resume.md,
type-checks it, and raises actionable errors when a bad edit would otherwise
crash further down the pipeline.
"""

from __future__ import annotations

import re
from pathlib import Path

from jobspy.model import Country

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import sys

    sys.exit(
        "error: reading the profile needs Python 3.11+ (tomllib).\n"
        f"       running {sys.version.split()[0]}. Recreate .venv with a newer Python."
    )

PROFILE_HEADER = r"^##\s+Job Search Profile\s*$"
TOML_FENCE = re.compile(r"```toml\s*\n(.*?)```", re.S)
NEXT_HEADING = re.compile(r"^##\s+\S", re.M)
MAX_CORE_KEYWORDS = 6


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