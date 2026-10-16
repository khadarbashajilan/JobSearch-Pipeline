"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def sample_resume_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "sample_resume.md"


@pytest.fixture
def sample_profile_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "sample_profile.md"


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch) -> Path:
    """A per-test SQLite database, isolated from the user's real state."""
    db = tmp_path / "state.db"
    monkeypatch.setenv("JOBSEARCH_DB", str(db))
    return db