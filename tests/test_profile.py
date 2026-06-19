from datetime import date
from pathlib import Path

import pytest

from src.profile import ProfileLoadError, load_profile


ROOT = Path(__file__).resolve().parents[1]


def test_example_profile_loads_and_has_verified_gpa() -> None:
    profile = load_profile(ROOT / "data" / "profile.example.yaml")

    assert profile.full_name == "Rafi Chowdhury"
    assert profile.education[0].gpa == 3.346
    assert profile.education[0].graduation_date == date(2028, 5, 1)
    assert profile.eligibility_preferences.first_generation is False
    assert profile.scholarship_preferences.minimum_award == 250


def test_document_paths_are_resolved_relative_to_yaml(tmp_path: Path) -> None:
    profile_file = tmp_path / "profile.yaml"
    profile_file.write_text(
        """
full_name: Test Student
address: {city: Arlington, state: TX}
education:
  - {school: UTA, degree: BS, major: Computer Science}
documents:
  resume: docs/resume.pdf
""",
        encoding="utf-8",
    )

    profile = load_profile(profile_file)

    assert profile.documents.resume == (tmp_path / "docs" / "resume.pdf").resolve()


def test_missing_profile_has_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ProfileLoadError, match="Profile file not found"):
        load_profile(tmp_path / "missing.yaml")
