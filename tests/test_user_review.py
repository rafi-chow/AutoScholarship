from src.db import ScholarshipDatabase
from src.models import Scholarship, UserStatus


def test_manual_promotion_persists(tmp_path) -> None:
    db = ScholarshipDatabase(tmp_path / "review.db"); db.initialize()
    item_id = db.add_scholarship(Scholarship(name="Review Me"))
    db.update_user_review(item_id, user_status=UserStatus.APPROVED_FOR_APPLY.value, user_notes="Eligibility checked")
    saved = db.get_scholarship(item_id)
    assert saved.user_status == UserStatus.APPROVED_FOR_APPLY
    assert saved.user_notes == "Eligibility checked"
    assert saved.reviewed_at is not None
