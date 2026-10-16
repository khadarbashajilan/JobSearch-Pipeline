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


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path: Path, monkeypatch) -> None:
    """Run every test from a temp dir so CLI defaults like ``saved/`` never
    write into the real repository, and point the default database at a
    temp file so no test touches ``~/.jobsearch/state.db``."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JOBSEARCH_DB", str(tmp_path / "state.db"))
