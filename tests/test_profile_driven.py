"""Tests for the profile-driven knobs: résumé skills, levels, years, retries, verify."""

from __future__ import annotations

from pathlib import Path

import pytest
import responses

from jobsearch.errors import ProfileError
from jobsearch.filtering import filter_jobs, infer_level, merge_level_aliases, required_years
from jobsearch.matching import candidate_level, match_job
from jobsearch.models import EmploymentType, LinkStatus, RawJob, StoredJob, WorkArrangement
from jobsearch.orchestrate import _fetch_with_retries
from jobsearch.profile import CandidateProfile, load_profile
from jobsearch.resume import extract_section_skills
from jobsearch.sources.base import SourceAdapter, SourceRequest
from jobsearch.verify import VerifyConfig, verify_url

RESUME = """# Someone

## Technical Skills

- **Languages:** Python, TypeScript, JavaScript (ES6+)
- **Frontend:** React.js, Tailwind CSS
- **Backend:** FastAPI, PostgreSQL

## Experience

- Built things with Django

## Job Search Profile

```toml
[titles]
primary = "Full-Stack Developer"

[search]
location = "Bengaluru, India"
country  = "India"
sites    = ["indeed"]
retries  = 2
retry_backoff_seconds = 0
linkedin_fetch_description = true

[keywords]
core = ["Python", "React"]
exclude_stack = ["Angular"]
level_filter = "junior"

[skills]
also  = ["Postgres"]
watch = ["Java", "Kubernetes"]

[experience]
years = 0.5
max_required_years = 2

[levels]
aliases = { "specialist" = "senior", "ii" = "ignore" }

[verify]
workers = 2
uncertain_hosts = ["blocked.example"]

[report]
previously_seen = "hide"
```
"""


def _write(tmp_path: Path, text: str = RESUME) -> Path:
    path = tmp_path / "resume.md"
    path.write_text(text, encoding="utf-8")
    return path


def _job(**overrides) -> StoredJob:
    base = dict(
        id="x",
        source="indeed",
        title="Full Stack Developer",
        company="Acme",
        employment_type=EmploymentType.FULL_TIME,
        work_arrangement=WorkArrangement.UNKNOWN,
        source_url="https://example.com/jobs/1",
    )
    base.update(overrides)
    return StoredJob(**base)


# --- profile -----------------------------------------------------------------


def test_resume_skills_section_is_parsed() -> None:
    skills = extract_section_skills(RESUME, "Technical Skills")
    assert skills == (
        "Python", "TypeScript", "JavaScript (ES6+)", "React.js",
        "Tailwind CSS", "FastAPI", "PostgreSQL",
    )


def test_profile_reads_every_new_setting(tmp_path: Path) -> None:
    profile = load_profile(_write(tmp_path))
    assert profile.core_keywords == ("Python", "React")
    # core + skills.also + résumé section, de-duplicated case-insensitively
    assert profile.skills[:3] == ("Python", "React", "Postgres")
    assert "React.js" in profile.skills and "Django" not in profile.skills
    assert profile.watch_skills == ("Java", "Kubernetes")
    assert profile.exclude_stack == ("Angular",)
    assert profile.experience_years == 0.5
    assert profile.max_required_years == 2.0
    assert profile.sites == ("indeed",)
    assert profile.retries == 2
    assert profile.linkedin_fetch_description is True
    assert profile.level_aliases == {"specialist": "senior", "ii": "ignore"}
    assert profile.verify_workers == 2
    assert profile.verify_uncertain_hosts == ("blocked.example",)
    assert profile.report_seen == "hide"


def test_profile_skills_section_can_be_disabled(tmp_path: Path) -> None:
    text = RESUME.replace("[skills]", '[skills]\nresume_section = ""')
    profile = load_profile(_write(tmp_path, text))
    assert "TypeScript" not in profile.skills


@pytest.mark.parametrize(
    "old, new, message",
    [
        ('sites    = ["indeed"]', 'sites = ["monster"]', "search.sites"),
        ('"ii" = "ignore"', '"ii" = "wizard"', "levels.aliases"),
        ("years = 0.5", 'years = "lots"', "years"),
        ('previously_seen = "hide"', 'previously_seen = "nope"', "report.previously_seen"),
    ],
)
def test_profile_rejects_bad_values(tmp_path: Path, old: str, new: str, message: str) -> None:
    with pytest.raises(ProfileError, match=message):
        load_profile(_write(tmp_path, RESUME.replace(old, new)))


# --- levels and years --------------------------------------------------------


def test_numbered_ladders_and_abbreviations() -> None:
    assert infer_level("Software Engineer II") == "mid"
    assert infer_level("Software Engineer III") == "senior"
    assert infer_level("Sr. Developer") == "senior"
    assert infer_level("Associate Software Engineer") == "junior"
    assert infer_level("Internal Tools Engineer") is None


def test_profile_aliases_override_and_disable_defaults() -> None:
    aliases = merge_level_aliases({"specialist": "senior", "ii": "ignore"})
    assert infer_level("Specialist software Engineer", aliases) == "senior"
    assert infer_level("Software Engineer II", aliases) is None


def test_required_years_from_title_then_description() -> None:
    assert required_years("Python Engineer | 6+ years Mandatory", None) == 6
    assert required_years("Dev", "Need 3-5 years of experience in Python.") == 3
    assert required_years("Dev", "3+ years experience in Python, 1+ year experience with React") == 1
    assert required_years("Dev", "Join a 20 year old company") is None


def test_filter_drops_stack_and_experience() -> None:
    jobs = [
        _job(id="a", title="Angular Developer"),
        _job(id="b", title="Python Fullstack Engineer | 6+ years Mandatory"),
        _job(id="c", title="Full Stack Developer", description="2+ years experience"),
    ]
    out, stats = filter_jobs(
        jobs, hours_old=None, exclude_keywords=(),
        exclude_stack=("Angular",), max_required_years=2,
    )
    assert [j.id for j in out] == ["c"]
    assert stats.dropped_excluded_stack == 1
    assert stats.dropped_experience_above == 1


# --- matching ----------------------------------------------------------------


def _profile(**overrides) -> CandidateProfile:
    base = dict(
        primary_title="Full-Stack Developer",
        skills=("Python", "React.js", "JavaScript (ES6+)"),
        watch_skills=("Java", "Kubernetes"),
        employment_types=("full_time",),
        experience_years=0.5,
        level_filter="junior",
    )
    base.update(overrides)
    return CandidateProfile(**base)


def test_skills_are_read_from_the_description() -> None:
    job = _job(description="We use React, Python and Kubernetes. JavaScript a plus.")
    match = match_job(job, _profile())
    assert set(match.matched_skills) == {"Python", "React.js", "JavaScript (ES6+)"}
    assert match.missing_skills == ("Kubernetes",)
    assert "no signals" not in match.explanation


def test_java_does_not_match_javascript() -> None:
    match = match_job(_job(description="Strong JavaScript skills"), _profile())
    assert "Java" not in match.missing_skills


def test_unacceptable_employment_type_is_a_hard_conflict() -> None:
    match = match_job(_job(employment_type=EmploymentType.CONTRACT), _profile())
    assert match.score == 0
    assert any("employment type" in c for c in match.hard_conflicts)


def test_candidate_level_prefers_explicit_then_ceiling_then_years() -> None:
    assert candidate_level(_profile(candidate_level="mid")) == "mid"
    assert candidate_level(_profile()) == "junior"
    assert candidate_level(_profile(level_filter="off", experience_years=0.5)) == "junior"


# --- retries -----------------------------------------------------------------


class _Flaky(SourceAdapter):
    name = "flaky"

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def fetch(self, request):
        self.calls += 1
        return self.results.pop(0)


_REQUEST = SourceRequest(query="q", queries=("q",), location="", country="", hours_old=None, results_wanted=1)
_RAW = RawJob(
    source="flaky", source_id="1", title="Dev", company="Acme", location=None,
    work_arrangement=WorkArrangement.UNKNOWN, employment_type=EmploymentType.UNKNOWN,
    source_url="https://example.com/1",
)


def test_empty_result_is_retried_with_backoff() -> None:
    adapter = _Flaky([[], [_RAW]])
    waits: list[float] = []
    found, diag = _fetch_with_retries(adapter, _REQUEST, retries=2, backoff=5, sleep=waits.append)
    assert adapter.calls == 2 and len(found) == 1
    assert waits == [5]
    assert "attempts=2" in diag.notes


def test_retries_stop_at_the_limit() -> None:
    adapter = _Flaky([[], []])
    found, diag = _fetch_with_retries(adapter, _REQUEST, retries=1, backoff=0, sleep=lambda _: None)
    assert adapter.calls == 2 and found == [] and diag.jobs_found == 0


# --- verification ------------------------------------------------------------


def test_blocked_board_is_uncertain_without_a_request() -> None:
    cfg = VerifyConfig(uncertain_hosts=("linkedin.com",))
    with responses.RequestsMock() as rsps:  # any request would raise
        result = verify_url("https://in.linkedin.com/jobs/view/1", config=cfg)
        assert len(rsps.calls) == 0
    assert result.status == LinkStatus.UNCERTAIN


@responses.activate
def test_redirect_to_private_address_is_never_requested() -> None:
    responses.add(
        responses.GET, "https://example.com/jobs/1",
        status=302, headers={"Location": "http://169.254.169.254/latest/meta-data/"},
    )
    result = verify_url("https://example.com/jobs/1", config=VerifyConfig())
    assert result.status == LinkStatus.UNREACHABLE
    assert "non-public" in result.evidence
    assert len(responses.calls) == 1


@responses.activate
def test_redirect_to_login_page_is_uncertain() -> None:
    responses.add(
        responses.GET, "https://example.com/jobs/1",
        status=302, headers={"Location": "https://example.com/login?next=/jobs/1"},
    )
    responses.add(responses.GET, "https://example.com/login", body="Sign in", status=200)
    result = verify_url("https://example.com/jobs/1", config=VerifyConfig(login_markers=("login",)))
    assert result.status == LinkStatus.UNCERTAIN


@responses.activate
def test_forbidden_is_uncertain_not_unreachable() -> None:
    responses.add(responses.GET, "https://example.com/jobs/1", status=403)
    result = verify_url("https://example.com/jobs/1", config=VerifyConfig())
    assert result.status == LinkStatus.UNCERTAIN


def test_checked_at_is_per_result() -> None:
    from jobsearch.verify import VerificationResult

    a = VerificationResult(status=LinkStatus.VERIFIED, evidence="")
    b = VerificationResult(status=LinkStatus.VERIFIED, evidence="")
    assert a.checked_at is not b.checked_at
    assert b.checked_at >= a.checked_at


def test_one_word_in_the_posting_counts_once() -> None:
    profile = _profile(skills=("React", "React.js", "Python"))
    match = match_job(_job(description="React and Python"), profile)
    assert match.matched_skills == ("React", "Python")


# --- locations, regions, level band -----------------------------------------


def test_profile_reads_locations_regions_and_band(tmp_path: Path) -> None:
    text = RESUME.replace(
        'location = "Bengaluru, India"',
        'locations = ["Bengaluru", "Chennai"]\nallowed_regions = ["Karnataka", "TN"]\nparallel_sources = 3',
    ).replace('level_filter = "junior"', 'level_filter = "junior"\nlevel_min = "junior"\nlevel_strict = true')
    profile = load_profile(_write(tmp_path, text))
    assert profile.locations == ("Bengaluru", "Chennai")
    assert profile.location == "Bengaluru"
    assert profile.allowed_regions == ("Karnataka", "TN")
    assert profile.parallel_sources == 3
    assert (profile.level_min, profile.level_filter, profile.level_strict) == ("junior", "junior", True)


def test_level_min_above_ceiling_is_rejected(tmp_path: Path) -> None:
    text = RESUME.replace('level_filter = "junior"', 'level_filter = "junior"\nlevel_min = "mid"')
    with pytest.raises(ProfileError, match="level_min"):
        load_profile(_write(tmp_path, text))


def test_boards_run_per_location_and_ats_once(monkeypatch) -> None:
    from jobsearch import orchestrate

    monkeypatch.setenv("JOBSEARCH_LEVER_BOARD", "acme")
    monkeypatch.delenv("JOBSEARCH_ASHBY_BOARD", raising=False)
    profile = _profile(locations=("Bengaluru", "Chennai"), location="Bengaluru")
    pairs = orchestrate.build_source_requests(profile, ["indeed", "lever", "ashby"])
    labels = [req.extra["label"] for _, req in pairs]
    assert labels == ["indeed @ Bengaluru", "indeed @ Chennai", "lever"]
    skipped = orchestrate.skipped_sources(["indeed", "lever", "ashby"])
    assert [d.source for d in skipped] == ["ashby"]
    assert "JOBSEARCH_ASHBY_BOARD" in skipped[0].error


def test_region_filter_keeps_south_india_remote_and_unknown() -> None:
    jobs = [
        _job(id="blr", location="Bengaluru, Karnataka, India"),
        _job(id="ka", location="KA, IN"),
        _job(id="pune", location="Pune, Maharashtra, India"),
        _job(id="remote", location="Remote"),
        _job(id="india", location="India"),
        _job(id="none", location=None),
    ]
    out, stats = filter_jobs(
        jobs, hours_old=None, exclude_keywords=(),
        allowed_regions=("Karnataka", "KA"), country="India",
    )
    assert [j.id for j in out] == ["blr", "ka", "remote", "india", "none"]
    assert stats.dropped_location == 1

    out, _ = filter_jobs(
        jobs, hours_old=None, exclude_keywords=(), allowed_regions=("Karnataka",),
        keep_remote=False, keep_unknown_location=False, country="India",
    )
    assert [j.id for j in out] == ["blr"]


def test_level_band_floor_and_strict() -> None:
    jobs = [
        _job(id="intern", title="Software Engineering Intern"),
        _job(id="entry", title="Entry Level Developer"),
        _job(id="fresher", title="Python Developer - Freshers"),
        _job(id="board", title="Developer", seniority="Entry level"),
        _job(id="years", title="Developer", description="0-2 years of experience"),
        _job(id="silent", title="Full Stack Developer"),
        _job(id="mid", title="Software Engineer II"),
    ]
    out, stats = filter_jobs(
        jobs, hours_old=None, exclude_keywords=(),
        level_filter="junior", level_min="junior", max_required_years=2,
    )
    assert [j.id for j in out] == ["entry", "fresher", "board", "years", "silent"]
    assert stats.dropped_level_below == 1 and stats.dropped_level_above == 1

    out, stats = filter_jobs(
        jobs, hours_old=None, exclude_keywords=(),
        level_filter="junior", level_min="junior", max_required_years=2, level_strict=True,
    )
    assert [j.id for j in out] == ["entry", "fresher", "board", "years"]
    assert stats.dropped_level_unknown == 1


def test_parallel_fetch_keeps_request_order(monkeypatch) -> None:
    import time as _time

    from jobsearch import orchestrate

    class _Slow(SourceAdapter):
        def __init__(self, name, delay):
            self.name, self.delay = name, delay

        def fetch(self, request):
            _time.sleep(self.delay)
            return [RawJob(
                source=self.name, source_id="1", title="Dev", company=self.name, location=None,
                work_arrangement=WorkArrangement.UNKNOWN, employment_type=EmploymentType.UNKNOWN,
                source_url=f"https://example.com/{self.name}",
            )]

    adapters = {"linkedin": _Slow("linkedin", 0.05), "indeed": _Slow("indeed", 0)}
    monkeypatch.setattr(orchestrate, "build_source", lambda site: adapters[site])
    profile = _profile(parallel_sources=2, level_filter="off", hours_old=None)
    _, _, report = orchestrate.run_pipeline(profile, ["linkedin", "indeed"], sleep=lambda _: None)
    assert [d.source for d in report.diagnostics] == ["linkedin", "indeed"]


def test_jobspy_rows_with_nan_fields_are_cleaned() -> None:
    from jobsearch.sources.jobspy_source import _drop_missing, _row_to_raw

    nan = float("nan")
    record = {
        "id": "li-1", "title": "Dev", "company": nan, "location": nan, "job_level": nan,
        "job_type": nan, "work_from_home_type": nan, "description": nan, "skills": nan,
        "job_url": "https://example.com/1", "company_url": nan, "job_url_direct": nan,
        "date_posted": nan, "is_remote": nan,
    }
    raw = _row_to_raw("glassdoor", _drop_missing(record))
    assert raw.company is None and raw.location is None and raw.seniority is None
    assert raw.description == "" and raw.employment_type == EmploymentType.UNKNOWN
