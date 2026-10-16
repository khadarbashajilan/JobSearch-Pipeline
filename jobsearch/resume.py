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