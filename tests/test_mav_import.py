from datetime import date
from pathlib import Path

from src.db import ScholarshipDatabase
from src.mav_import import import_mav_opportunity, mav_source
from src.models import Recommendation
from src.policy import AccessMode
from src.profile import load_profile


ROOT = Path(__file__).resolve().parents[1]


def test_mav_source_is_manual_only() -> None:
    source = mav_source(ROOT / "data" / "sources.yaml")

    assert source.category == "uta_manual"
    assert source.access_mode == AccessMode.MANUAL_ONLY


def test_mav_manual_import_parses_ranks_and_persists(tmp_path: Path) -> None:
    database = ScholarshipDatabase(tmp_path / "mav.db")
    database.initialize()
    profile = load_profile(ROOT / "data" / "profile.example.yaml")
    text = """
    Name: UTA Computer Science Leadership Scholarship
    Amount: $1,500
    Deadline: December 1, 2026
    Eligibility:
    Must be an undergraduate enrolled at the University of Texas at Arlington.
    Computer Science and Engineering majors are eligible.
    Essay Prompt:
    Describe a leadership experience and what you learned.
    """

    record = import_mav_opportunity(
        text,
        database=database,
        profile=profile,
        sources_path=ROOT / "data" / "sources.yaml",
        today=date(2026, 6, 19),
    )

    assert record.source_category == "uta_manual"
    assert record.source_type == "manual"
    assert record.essay_prompts == ["Describe a leadership experience and what you learned."]
    assert record.ranking is not None
    assert record.ranking.recommendation == Recommendation.APPLY
    assert database.get_scholarship(record.id) == record

