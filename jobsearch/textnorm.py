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

    "Whole token" means a word-boundary match after normalization, which
    prevents "Java" matching "JavaScript" and similar substring traps.
    """
    if not haystack or not needles:
        return ()
    text = haystack.lower()
    found = []
    for needle in needles:
        if not needle:
            continue
        token = needle.lower()
        if re.search(rf"\b{re.escape(token)}\b", text):
            found.append(token)
    return tuple(found)