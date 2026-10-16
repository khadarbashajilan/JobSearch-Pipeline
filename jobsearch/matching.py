"""Deterministic résumé-to-job matching.

The matching engine is intentionally rule-based so it is reproducible and
testable. The optional LLM provider only contributes a sentence of
explanation; it never overrides the numeric score.

Score composition (each component clamped to [0, 1]):

- **skill_overlap (40%)**: ``|matched ∩ required| / |required|`` for
  required skills; if the job does not list any required skills, this
  component is neutral (0.5), because the absence of evidence is not
  evidence of absence.
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

import re
from dataclasses import dataclass

from jobsearch.models import (
    EmploymentType,
    JobMatch,
    StoredJob,
    WorkArrangement,
)
from jobsearch.profile import CandidateProfile
from jobsearch.textnorm import contains_any, normalize_title


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


def _seniority_required(job: StoredJob) -> str | None:
    """Best-effort seniority extraction from title + description."""
    text = " ".join(filter(None, [job.title, job.seniority or ""]))
    lowered = text.lower()
    for level in ("principal", "staff", "lead", "senior", "mid", "junior", "intern"):
        if re.search(rf"\b{level}\b", lowered):
            return level
    return None


_SENIORITY_RANK = {
    "intern": 0,
    "junior": 1,
    "mid": 2,
    "senior": 3,
    "lead": 4,
    "staff": 5,
    "principal": 6,
}


def _seniority_score(candidate: CandidateProfile, required: str | None) -> float:
    """Return 1.0 when no seniority is required, 0.5 when unknown, partial otherwise."""
    if not required:
        return 1.0
    if candidate.experience_years is None:
        return 0.5
    target = _SENIORITY_RANK.get(required, 2)
    candidate_level = (
        2
        if candidate.experience_years < 2
        else 3
        if candidate.experience_years < 5
        else 4
        if candidate.experience_years < 8
        else 5
    )
    if candidate_level >= target:
        return 1.0
    return max(0.0, 1.0 - 0.25 * (target - candidate_level))


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


def _experience_score(candidate: CandidateProfile, required_years: int | None) -> float:
    if required_years is None or required_years <= 0:
        return 0.5
    if candidate.experience_years is None:
        return 0.5
    return 1.0 if candidate.experience_years >= required_years else 0.0


def _required_years(job: StoredJob) -> int | None:
    text = job.description or ""
    match = re.search(r"(\d+)\+?\s*(?:years?|yrs?)\s+(?:of\s+)?experience", text, re.I)
    if match:
        return int(match.group(1))
    return None


def _skill_overlap(
    job: StoredJob, candidate_skills: tuple[str, ...]
) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    required = tuple(s.lower() for s in job.required_skills)
    if not required:
        return 0.5, (), ()
    matched = contains_any(" ".join(job.required_skills), candidate_skills)
    missing = tuple(r for r in required if r not in matched)
    return len(matched) / len(required), matched, missing


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
    skills_score, matched, missing = _skill_overlap(job, profile.skills)
    title_score = _title_alignment(job, profile.all_titles())
    seniority_required = _seniority_required(job)
    seniority_score = _seniority_score(profile, seniority_required)
    location_score = _location_score(profile, job)
    employment_score = _employment_score(profile, job)
    experience_score = _experience_score(profile, _required_years(job))

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

    score = components.weighted() if not hard_conflicts else 0.0
    explanation = _explain(components, matched, missing, seniority_required)

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
) -> str:
    bits: list[str] = []
    if matched:
        bits.append(f"matched: {', '.join(matched)}")
    if missing:
        bits.append(f"missing: {', '.join(missing)}")
    if seniority_required:
        bits.append(f"seniority={seniority_required}")
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