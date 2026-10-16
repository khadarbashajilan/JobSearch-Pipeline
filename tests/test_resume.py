"""Tests for résumé extraction."""

from __future__ import annotations

import json

import pytest

from jobsearch.errors import ResumeParseError
from jobsearch.resume import parse_resume


def test_parse_markdown_extracts_bulleted_skills(fixtures_dir) -> None:
    path = fixtures_dir / "sample_resume.md"
    parsed = parse_resume(path)
    assert "Python" in parsed.skills
    assert "FastAPI" in parsed.skills


def test_parse_markdown_strips_markdown_to_text(fixtures_dir) -> None:
    parsed = parse_resume(fixtures_dir / "sample_resume.md")
    # Headings stripped, code fences collapsed — text contains prose.
    assert "python" in parsed.raw_text.lower()


def test_parse_json_extracts_structured_fields(tmp_path) -> None:
    path = tmp_path / "resume.json"
    path.write_text(
        json.dumps(
            {
                "skills": ["Python", "FastAPI"],
                "titles": ["GenAI Developer"],
                "years_experience": 4,
                "education": ["B.Sc. CS"],
                "work_history": ["Engineer at Acme"],
            }
        ),
        encoding="utf-8",
    )
    parsed = parse_resume(path)
    assert "Python" in parsed.skills
    assert parsed.years_experience == 4
    assert "B.Sc. CS" in parsed.education


def test_parse_unsupported_format(tmp_path) -> None:
    path = tmp_path / "resume.pdf"
    path.write_text("not real pdf", encoding="utf-8")
    with pytest.raises(ResumeParseError, match="unsupported"):
        parse_resume(path)


def test_parse_missing_file(tmp_path) -> None:
    with pytest.raises(ResumeParseError, match="not found"):
        parse_resume(tmp_path / "missing.md")


def test_parse_json_malformed(tmp_path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ResumeParseError, match="malformed"):
        parse_resume(path)