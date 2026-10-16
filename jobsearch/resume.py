"""Résumé extraction is intentionally minimal in this revision.

The supported formats are plain Markdown (the format ``resume.md`` ships in)
and JSON (a small sanitized fixture for tests). PDF and DOCX are out of
scope here: extracting them well requires heavyweight, OS-specific
dependencies we have not justified. The interface is shaped so adding
PDF/DOCX support later only touches this module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from jobsearch.errors import ResumeParseError


@dataclass
class ParsedResume:
    """Structured form of the candidate's résumé.

    ``facts`` are values lifted directly from the document; ``preferences``
    are filled by ``profile.py`` from the TOML block in the same file.
    """

    raw_text: str
    skills: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()
    years_experience: int | None = None
    education: tuple[str, ...] = ()
    work_history: tuple[str, ...] = ()
    extra: dict[str, str] = field(default_factory=dict)


_SKILL_LINE_RE = re.compile(r"^\s*(?:[-*•]|\d+\.)\s*(.+?)\s*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def _strip_markdown_to_text(markdown: str) -> str:
    """Reduce a Markdown document to readable text.

    Not a full renderer — the goal is just enough text that downstream
    keyword matching has something to work with. Keeping it simple avoids
    pulling in another dependency for a marginal improvement.
    """
    text = re.sub(r"```.*?```", " ", markdown, flags=re.DOTALL)
    text = re.sub(r"`[^`]+`", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_>#~]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_resume(path: Path) -> ParsedResume:
    """Parse a résumé file.

    Supports ``.md``, ``.markdown``, and ``.json``. Anything else raises
    :class:`ResumeParseError` so the CLI surfaces a useful message instead
    of a traceback.
    """
    if not path.is_file():
        raise ResumeParseError(f"résumé not found: {path}")
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ResumeParseError(f"could not read résumé: {path} ({exc})") from exc

    if suffix in (".md", ".markdown"):
        return _parse_markdown(text)
    if suffix == ".json":
        return _parse_json(text)
    raise ResumeParseError(
        f"unsupported résumé format: {suffix} "
        "(supported: .md, .markdown, .json)"
    )


def _parse_markdown(text: str) -> ParsedResume:
    plain = _strip_markdown_to_text(text)
    skills: list[str] = []
    titles: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*", "•")):
            value = stripped.lstrip("-*• ").strip()
            if value:
                skills.append(value)
        lowered = stripped.lower()
        if any(
            lowered.startswith(prefix)
            for prefix in ("title:", "role:", "position:")
        ):
            value = stripped.split(":", 1)[1].strip()
            if value:
                titles.append(value)
    return ParsedResume(
        raw_text=plain,
        skills=tuple(skills),
        titles=tuple(titles),
    )


def _parse_json(text: str) -> ParsedResume:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ResumeParseError(f"résumé JSON is malformed: {exc}") from exc
    if not isinstance(data, dict):
        raise ResumeParseError("résumé JSON must be an object")
    return ParsedResume(
        raw_text=json.dumps(data, ensure_ascii=False),
        skills=tuple(data.get("skills") or ()),
        titles=tuple(data.get("titles") or ()),
        years_experience=(
            int(data["years_experience"])
            if isinstance(data.get("years_experience"), (int, float))
            else None
        ),
        education=tuple(data.get("education") or ()),
        work_history=tuple(data.get("work_history") or ()),
    )

_SECTION_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_BOLD_LABEL_RE = re.compile(r"^\*\*[^*]+\*\*\s*:?\s*|^[^:,]{1,60}:\s+")


def find_skills_heading(markdown: str, exclude: tuple[str, ...] = ()) -> str | None:
    """Return the first ``## `` heading whose text mentions "skill"."""
    for match in _SECTION_HEADING_RE.finditer(markdown):
        heading = match.group(1).strip()
        if "skill" in heading.lower() and heading.lower() not in {e.lower() for e in exclude}:
            return heading
    return None


def extract_section_skills(markdown: str, heading: str) -> tuple[str, ...]:
    """Pull a flat skill list out of one ``## <heading>`` section.

    Each bullet may carry a label (``- **Frontend:** React, Vite``); the
    label is dropped and the remainder is split on commas. Parenthetical
    qualifiers stay in the value — :func:`textnorm.skill_variants` handles
    them at match time so the report shows the résumé's own wording.
    Returns ``()`` when the heading is absent.
    """
    if not heading:
        return ()
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE | re.IGNORECASE)
    start = pattern.search(markdown)
    if not start:
        return ()
    body = markdown[start.end():]
    nxt = re.search(r"^#{1,2}\s+\S", body, re.MULTILINE)
    section = body[: nxt.start()] if nxt else body

    skills: list[str] = []
    seen: set[str] = set()
    for line in section.splitlines():
        stripped = line.strip()
        bullet = re.match(r"^[-*•]\s+", stripped)
        if not bullet:
            continue
        value = stripped[bullet.end():].strip()
        value = _BOLD_LABEL_RE.sub("", value, count=1)
        # Commas inside parentheses ("Go (Gin, Echo)") are not separators.
        for item in re.split(r",(?![^()]*\))", value):
            item = item.strip().strip("*_`").strip()
            if item and item.lower() not in seen:
                seen.add(item.lower())
                skills.append(item)
    return tuple(skills)
