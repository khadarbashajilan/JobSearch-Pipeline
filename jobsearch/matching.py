"""Deterministic résumé-to-job matching.

The matching engine is intentionally rule-based so it is reproducible and
testable. Every vocabulary it uses comes from the profile: the candidate's
skills (``keywords.core``, ``skills.also`` and the résumé's skills
section) and the technologies they track but lack (``skills.watch``).

Score composition (each component clamped to [0, 1]):

- **skill_overlap (40%)**: the job's required skills are the vocabulary
  terms found in its title + description (plus any the board listed);
  the score is the share the candidate has. If no vocabulary term
  appears, the component is neutral (0.5), because the absence of
  evidence is not evidence of absence.
- **title_alignment (20%)**: whole-token overlap of the job title with
  any of the candidate's titles.
- **seniority (15%)**: explicitly required seniority level met, or
  marked unknown if not stated.
- **location (10%)**: explicit location or remote-preference match.
- **employment_type (10%)**: candidate's acceptable types intersect the
  job's type.
- **experience (5%)**: years of experience vs. stated requirement.

Hard conflicts (e.g. location impossible, employment type unacceptable)
short-circuit with ``score = 0`` and are surfaced in ``hard_conflicts``.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobsearch.filtering import (
    SENIORITY_RANK,
    infer_level,
    merge_level_aliases,
    required_years,
)
from jobsearch.models import (
    EmploymentType,
    JobMatch,
    StoredJob,
    WorkArrangement,
)
from jobsearch.profile import CandidateProfile
from jobsearch.textnorm import has_token, normalize_title, skill_variants


@dataclass
class _ComponentScores:
    skill_overlap: float
    title_alignment: float
    seniority: float
    location: float
    employment_type: float
    experience: float

    def weighted(self) -> float:
        return (
            0.40 * self.skill_overlap
            + 0.20 * self.title_alignment
            + 0.15 * self.seniority
            + 0.10 * self.location
            + 0.10 * self.employment_type
            + 0.05 * self.experience
        )


def _seniority_required(job: StoredJob, aliases: dict[str, str]) -> str | None:
    """Seniority the posting asks for, from its title, then the board's level field."""
    return infer_level(job.title, aliases) or infer_level(job.seniority or "", aliases)


def candidate_level(candidate: CandidateProfile) -> str | None:
    """The candidate's own rung on the ladder.

    ``experience.level`` wins; otherwise the ``level_filter`` ceiling (the
    candidate told us the highest level they want); otherwise an estimate
    from ``experience.years``.
    """
    if candidate.candidate_level:
        return candidate.candidate_level
    if candidate.level_filter and candidate.level_filter != "off":
        return candidate.level_filter
    years = candidate.experience_years
    if years is None:
        return None
    if years < 2:
        return "junior"
    if years < 5:
        return "mid"
    if years < 8:
        return "senior"
    return "lead"


def _seniority_score(candidate: CandidateProfile, required: str | None) -> float:
    """1.0 when nothing is required or the candidate meets it; partial otherwise."""
    if not required:
        return 1.0
    level = candidate_level(candidate)
    if level is None:
        return 0.5
    target = SENIORITY_RANK.get(required, 2)
    have = SENIORITY_RANK.get(level, 2)
    if have >= target:
        return 1.0
    return max(0.0, 1.0 - 0.25 * (target - have))


def _location_score(candidate: CandidateProfile, job: StoredJob) -> float:
    pref = candidate.remote_preference
    if job.work_arrangement == WorkArrangement.REMOTE:
        return 1.0 if pref in {"remote", "any"} else 0.0
    if pref == "any":
        return 0.6
    if pref == "remote":
        return 0.2 if job.work_arrangement == WorkArrangement.HYBRID else 0.1
    if pref == "onsite":
        return 1.0 if job.work_arrangement == WorkArrangement.ONSITE else 0.5
    if pref == "hybrid":
        return 1.0 if job.work_arrangement == WorkArrangement.HYBRID else 0.5
    return 0.5


def _employment_score(candidate: CandidateProfile, job: StoredJob) -> float:
    if job.employment_type == EmploymentType.UNKNOWN:
        return 0.5
    acceptable = set(candidate.employment_types)
    return 1.0 if job.employment_type.value in acceptable else 0.0


def _experience_score(candidate: CandidateProfile, required: float | None) -> float:
    if required is None or required <= 0:
        return 0.5
    if candidate.experience_years is None:
        return 0.5
    if candidate.experience_years >= required:
        return 1.0
    return max(0.0, 1.0 - 0.25 * (required - candidate.experience_years))


def _find_skills(text: str, vocabulary: tuple[str, ...]) -> list[str]:
    """Vocabulary entries (in their profile spelling) present in ``text``.

    A spelling is counted once: if "React" already matched, "React.js"
    (whose variants include "React") does not count the same word again.
    """
    lowered = text.lower()
    claimed: set[str] = set()
    found: list[str] = []
    for skill in vocabulary:
        variants = {v.lower() for v in skill_variants(skill)}
        hits = {v for v in variants if has_token(lowered, v)}
        if hits and not hits & claimed:
            found.append(skill)
        claimed |= hits
    return found


def _skill_overlap(
    job: StoredJob, candidate: CandidateProfile
) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    """Share of the job's required skills the candidate has.

    Required = skills the board listed + every profile vocabulary term
    (candidate skills and ``skills.watch``) that appears in the posting.
    """
    text = " ".join(filter(None, [job.title, job.description]))
    have = _find_skills(text, candidate.skills)
    watched = [
        s for s in _find_skills(text, candidate.watch_skills)
        if s.lower() not in {h.lower() for h in have}
    ]
    have_keys = {v.lower() for skill in candidate.skills for v in skill_variants(skill)}
    listed_missing = [
        s for s in job.required_skills
        if s.lower() not in have_keys and s.lower() not in {w.lower() for w in watched}
    ]
    listed_have = [
        s for s in job.required_skills
        if s.lower() in have_keys and s.lower() not in {h.lower() for h in have}
    ]
    matched = tuple(have + listed_have)
    missing = tuple(watched + listed_missing)
    total = len(matched) + len(missing)
    if not total:
        return 0.5, (), ()
    return len(matched) / total, matched, missing


def _title_alignment(job: StoredJob, candidate_titles: tuple[str, ...]) -> float:
    if not candidate_titles:
        return 0.0
    job_title = normalize_title(job.title)
    best = 0.0
    for candidate_title in candidate_titles:
        if not candidate_title:
            continue
        ct = normalize_title(candidate_title)
        if not ct:
            continue
        if ct in job_title or job_title in ct:
            best = max(best, 1.0)
        else:
            overlap = set(ct.split()) & set(job_title.split())
            if overlap:
                best = max(best, len(overlap) / max(len(ct.split()), 1))
    return best


def match_job(job: StoredJob, profile: CandidateProfile) -> JobMatch:
    """Score one job against the candidate profile."""
    aliases = merge_level_aliases(profile.level_aliases)
    skills_score, matched, missing = _skill_overlap(job, profile)
    title_score = _title_alignment(job, profile.all_titles())
    seniority_required = _seniority_required(job, aliases)
    seniority_score = _seniority_score(profile, seniority_required)
    location_score = _location_score(profile, job)
    employment_score = _employment_score(profile, job)
    years_required = required_years(job.title, job.description)
    experience_score = _experience_score(profile, years_required)

    components = _ComponentScores(
        skill_overlap=skills_score,
        title_alignment=title_score,
        seniority=seniority_score,
        location=location_score,
        employment_type=employment_score,
        experience=experience_score,
    )

    hard_conflicts: list[str] = []
    if (
        profile.remote_preference == "remote"
        and job.work_arrangement == WorkArrangement.ONSITE
    ):
        hard_conflicts.append("requires on-site; candidate wants remote")
    if (
        job.employment_type != EmploymentType.UNKNOWN
        and profile.employment_types
        and job.employment_type.value not in profile.employment_types
    ):
        hard_conflicts.append(
            f"employment type {job.employment_type.value}; candidate accepts "
            f"{', '.join(profile.employment_types)}"
        )

    score = components.weighted() if not hard_conflicts else 0.0
    explanation = _explain(components, matched, missing, seniority_required, years_required)

    return JobMatch(
        job=job,
        score=round(score, 3),
        matched_skills=matched,
        missing_skills=missing,
        hard_conflicts=tuple(hard_conflicts),
        explanation=explanation,
    )


def _explain(
    components: _ComponentScores,
    matched: tuple[str, ...],
    missing: tuple[str, ...],
    seniority_required: str | None,
    years_required: float | None = None,
) -> str:
    bits: list[str] = []
    if matched:
        bits.append(f"matched: {', '.join(matched)}")
    if missing:
        bits.append(f"missing: {', '.join(missing)}")
    if seniority_required:
        bits.append(f"seniority={seniority_required}")
    if years_required is not None:
        bits.append(f"asks {years_required:g}+ yrs")
    if not bits:
        bits.append("no signals found in the posting")
    return "; ".join(bits)


def rank_matches(
    jobs: list[StoredJob], profile: CandidateProfile
) -> list[JobMatch]:
    """Score and sort jobs by descending match score."""
    matches = [match_job(job, profile) for job in jobs]
    matches.sort(key=lambda m: (m.score, len(m.matched_skills)), reverse=True)
    return matches