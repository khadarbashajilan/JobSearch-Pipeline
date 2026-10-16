"""Tests for profile parsing and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from jobsearch.errors import ProfileError
from jobsearch.profile import CandidateProfile, load_profile


def test_load_profile_extracts_toml_block(sample_profile_path: Path) -> None:
    profile = load_profile(sample_profile_path)
    assert isinstance(profile, CandidateProfile)
    assert profile.primary_title == "GenAI Developer"
    assert "Generative AI Engineer" in profile.alternative_titles
    assert profile.location == "Bengaluru, India"
    assert profile.jobspy_country == "india"
    assert "Python" in profile.skills
    assert "Senior" in profile.exclude_keywords
    assert profile.experience_years == 4
    assert profile.employment_types == ("full_time",)


def test_load_profile_resume_path_missing() -> None:
    with pytest.raises(ProfileError, match="resume not found"):
        load_profile(Path("/tmp/does-not-exist.md"))


def test_load_profile_missing_section(tmp_path: Path) -> None:
    bad = tmp_path / "no_profile.md"
    bad.write_text("# Just a title\n\nNo toml here.\n", encoding="utf-8")
    with pytest.raises(ProfileError, match="no '## Job Search Profile'"):
        load_profile(bad)


def test_load_profile_malformed_toml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.md"
    bad.write_text(
        "## Job Search Profile\n\n```toml\nthis is not = = toml\n```\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="malformed toml"):
        load_profile(bad)


def test_load_profile_stops_at_next_heading(tmp_path: Path) -> None:
    """A stray TOML block further down the resume must be ignored."""
    bad = tmp_path / "two_toml.md"
    bad.write_text(
        "## Job Search Profile\n\n```toml\n"
        "[titles]\nprimary = \"Real One\"\n"
        "[search]\nlocation = \"Here\"\n"
        "[keywords]\ncore = [\"a\", \"b\"]\n"
        "```\n\n"
        "## Notes\n\n```toml\n"
        "[titles]\nprimary = \"Should not win\"\n"
        "```\n",
        encoding="utf-8",
    )
    profile = load_profile(bad)
    assert profile.primary_title == "Real One"


def test_load_profile_minimum_skills_enforced(tmp_path: Path) -> None:
    bad = tmp_path / "no_skills.md"
    bad.write_text(
        "## Job Search Profile\n\n```toml\n"
        "[titles]\nprimary = \"X\"\n"
        "[search]\nlocation = \"Here\"\n"
        "[keywords]\ncore = [\"only-one\"]\n"
        "```\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="keywords.core"):
        load_profile(bad)


def test_load_profile_rejects_invalid_remote(tmp_path: Path) -> None:
    bad = tmp_path / "bad_remote.md"
    bad.write_text(
        "## Job Search Profile\n\n```toml\n"
        "[titles]\nprimary = \"X\"\n"
        "[search]\nlocation = \"Here\"\nremote = \"anywhere-but-actually-bad\"\n"
        "[keywords]\ncore = [\"a\", \"b\"]\n"
        "```\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="search.remote"):
        load_profile(bad)


def test_load_profile_rejects_unknown_country(tmp_path: Path) -> None:
    bad = tmp_path / "bad_country.md"
    bad.write_text(
        "## Job Search Profile\n\n```toml\n"
        "[titles]\nprimary = \"X\"\n"
        "[search]\nlocation = \"Here\"\ncountry = \"Atlantis\"\n"
        "[keywords]\ncore = [\"a\", \"b\"]\n"
        "```\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="search.country"):
        load_profile(bad)


def test_load_profile_accepts_level_filter(tmp_path: Path) -> None:
    md = tmp_path / "level.md"
    md.write_text(
        "## Job Search Profile\n\n```toml\n"
        "[titles]\nprimary = \"X\"\n"
        "[search]\nlocation = \"Here\"\n"
        "[keywords]\ncore = [\"a\", \"b\"]\nlevel_filter = \"junior\"\n"
        "```\n",
        encoding="utf-8",
    )
    profile = load_profile(md)
    assert profile.level_filter == "junior"


def test_load_profile_defaults_level_filter_to_off(tmp_path: Path) -> None:
    md = tmp_path / "default.md"
    md.write_text(
        "## Job Search Profile\n\n```toml\n"
        "[titles]\nprimary = \"X\"\n"
        "[search]\nlocation = \"Here\"\n"
        "[keywords]\ncore = [\"a\", \"b\"]\n"
        "```\n",
        encoding="utf-8",
    )
    profile = load_profile(md)
    assert profile.level_filter == "off"


def test_load_profile_rejects_invalid_level_filter(tmp_path: Path) -> None:
    md = tmp_path / "bad_level.md"
    md.write_text(
        "## Job Search Profile\n\n```toml\n"
        "[titles]\nprimary = \"X\"\n"
        "[search]\nlocation = \"Here\"\n"
        "[keywords]\ncore = [\"a\", \"b\"]\nlevel_filter = \"executive\"\n"
        "```\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="keywords.level_filter"):
        load_profile(md)