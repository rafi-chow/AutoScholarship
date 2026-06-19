from datetime import date

from src.db import ScholarshipDatabase
from src.models import Scholarship, ScholarshipStatus
from src.profile import load_profile
from src.ranker import rank_scholarship


def test_database_round_trip_with_ranking(tmp_path) -> None:
    database = ScholarshipDatabase(tmp_path / "scholarships.db")
    database.initialize()
    scholarship = Scholarship(
        name="Texas Engineering Award",
        provider="Example Foundation",
        amount=1500,
        deadline=date(2026, 9, 1),
        eligibility=["Texas undergraduate"],
        major_restrictions=["Computer Science or Engineering"],
        status=ScholarshipStatus.APPLY_NOW,
        application_url="https://example.org/apply",
    )
    scholarship_id = database.add_scholarship(scholarship)
    saved = scholarship.model_copy(update={"id": scholarship_id})
    profile = load_profile("data/profile.example.yaml")
    database.save_ranking(rank_scholarship(profile, saved, today=date(2026, 6, 19)))

    record = database.get_scholarship(scholarship_id)

    assert record is not None
    assert record.name == scholarship.name
    assert record.application_url is not None
    assert record.ranking is not None
    assert record.ranking.scholarship_id == scholarship_id
    assert database.list_scholarships(ScholarshipStatus.APPLY_NOW) == [record]

