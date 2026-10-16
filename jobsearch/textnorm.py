"""Text normalization helpers shared by matching, filtering, and dedup.

Small, pure functions — no IO, no globals — so they can be exercised in
unit tests without spinning up any infrastructure.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

_WHITESPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^\w\s]")


def normalize_title(text: str) -> str:
    """Return a lowercased, punctuation-stripped form for comparison."""
    if not text:
        return ""
    lowered = text.lower()
    cleaned = _NON_WORD_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def parse_iso_datetime(value: object) -> datetime | None:
    """Parse a timestamp defensively.

    Accepts ISO-8601 strings and ``datetime`` objects. Naive datetimes are
    assumed to be UTC so they sort correctly alongside tz-aware ones.
    Returns ``None`` for unparseable or empty input — never raises.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # ``fromisoformat`` handles offsets like "+00:00" but not "Z".
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def contains_any(haystack: str, needles: tuple[str, ...]) -> tuple[str, ...]:
    """Return the subset of ``needles`` found as whole tokens in ``haystack``.

    "Whole token" means the needle is not flanked by word characters, which
    prevents "Java" matching "JavaScript" and similar substring traps. It
    uses look-arounds rather than ``\b`` so needles that start or end with
    punctuation (".NET", "C++", "C#") still match.
    """
    if not haystack or not needles:
        return ()
    text = haystack.lower()
    found = []
    for needle in needles:
        if not needle:
            continue
        token = needle.lower()
        if has_token(text, token):
            found.append(token)
    return tuple(found)


def has_token(text: str, token: str) -> bool:
    """True when ``token`` occurs in ``text`` as a whole token (case-insensitive)."""
    if not text or not token:
        return False
    return re.search(rf"(?<!\w){re.escape(token.lower())}(?!\w)", text.lower()) is not None


def skill_variants(skill: str) -> tuple[str, ...]:
    """Spellings of ``skill`` worth searching for in a job description.

    Parenthetical qualifiers are dropped ("JavaScript (ES6+)" → "JavaScript")
    and a trailing ".js" is optional ("React.js" also matches "React"). The
    rules are spelling-level only; anything vocabulary-level (synonyms such
    as Postgres/PostgreSQL) belongs in ``skills.also`` in the profile.
    """
    base = re.sub(r"\([^)]*\)", " ", skill)
    base = _WHITESPACE_RE.sub(" ", base).strip()
    if not base:
        return ()
    variants = [base]
    lowered = base.lower()
    if lowered.endswith(".js") and len(base) > 3:
        variants.append(base[:-3])
    return tuple(dict.fromkeys(variants))