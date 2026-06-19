from src.models import Scholarship
from src.policy import submission_allowed


def test_submission_requires_both_approvals() -> None:
    default = Scholarship(name="Default")
    approved = Scholarship(name="Approved", pre_approved_submit=True)

    assert submission_allowed(default, site_allowed=True) is False
    assert submission_allowed(approved, site_allowed=False) is False
    assert submission_allowed(approved, site_allowed=True) is True

