"""Query derivation from a :class:`CandidateProfile`.

Boards parse queries differently. Indeed understands boolean operators;
LinkedIn and the rest treat the search box as plain text. This module
produces one query per capability so the scraper can group sites by the
form they accept. Alternative titles are folded into the plain query so
``titles.also`` actually participates in discovery instead of being
documentation.
"""

from __future__ import annotations

from jobsearch.profile import CandidateProfile

MAX_BOOLEAN_TERMS = 6


def build_queries(profile: CandidateProfile) -> dict[str, str]:
    """Return one query per site capability.

    Returns a dict with two keys:

    - ``"indeed"``: boolean form, e.g. ``'"GenAI Developer" (Python OR
      FastAPI OR LLM) -Senior'``.
    - ``"plain"``: flat keyword string used for every other site. Alternative
      titles are included here so the search pool widens without exploding.
    """
    primary = profile.primary_title
    boolean = f'"{primary}" ({" OR ".join(profile.skills[:MAX_BOOLEAN_TERMS])})'
    if profile.exclude_keywords:
        boolean += " " + " ".join(f"-{e}" for e in profile.exclude_keywords)

    plain_terms = [primary, *profile.skills, *profile.alternative_titles]
    plain = " ".join(dict.fromkeys(t for t in plain_terms if t))

    override = profile.raw_queries.get("indeed", "").strip()
    return {
        "indeed": override or boolean,
        "plain": plain,
    }


def parse_sites(raw: str) -> list[str]:
    """Parse the ``--site`` value into a list of normalised site tokens."""
    tokens = [token.strip().lower() for token in raw.split(",") if token.strip()]
    if not tokens:
        raise ValueError("--site: no sites given")
    valid = {"linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "bayt", "naukri", "bdjobs", "greenhouse", "lever", "ashby"}
    unknown = [t for t in tokens if t not in valid]
    if unknown:
        raise ValueError(
            f"unknown site(s): {', '.join(unknown)}. Valid: {', '.join(sorted(valid))}"
        )
    return tokens