from pathlib import Path

import pytest

from src.export import export_application_packet
from src.models import CandidateType, Recommendation, RankingResult, Scholarship, ScholarshipRecord, ScholarshipStatus, ScoreBreakdown, UserStatus
from src.quality import classify_candidate, queue_status


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (Scholarship(name="Scholarship explainer video", source_url="https://youtube.com/watch?v=1"), CandidateType.VIDEO_SOCIAL_NEWS),
        (Scholarship(name="Top Scholarships for STEM Students", source_url="https://site.test/blog/top-scholarships"), CandidateType.BLOG_ARTICLE_GUIDE),
        (Scholarship(name="Doctoral Dissertation Fellowship", source_url="https://example.edu/fellowship"), CandidateType.GRADUATE_ONLY),
        (Scholarship(name="Scholarship Opportunities", source_url="https://example.org/scholarships"), CandidateType.DIRECTORY_LIST),
    ],
)
def test_hard_quality_classifications(item: Scholarship, expected: CandidateType) -> None:
    assert classify_candidate(item).candidate_type == expected


def test_other_school_only_is_skipped() -> None:
    item = classify_candidate(Scholarship(
        name="Gator Student Scholarship",
        source_url="https://ufl.edu/scholarships/gator",
        eligibility=["Must be currently enrolled at the University of Florida"],
    ))
    assert item.candidate_type == CandidateType.OTHER_SCHOOL_ONLY
    assert queue_status(item, Recommendation.APPLY) == ScholarshipStatus.SKIPPED


def test_direct_complete_scholarship_becomes_apply_now() -> None:
    item = classify_candidate(Scholarship(
        name="Texas Software Engineering Scholarship",
        amount=2500,
        deadline="2027-03-01",
        eligibility=["Texas undergraduate computer science students"],
        application_url="https://apply.smapply.io/prog/texas-software",
        source_url="https://foundation.test/texas-software-scholarship",
    ))
    assert item.candidate_type == CandidateType.DIRECT_APPLICATION
    assert item.confidence_score >= 60
    assert queue_status(item, Recommendation.APPLY) == ScholarshipStatus.APPLY_NOW


def test_direct_no_essay_becomes_quick_apply() -> None:
    item = classify_candidate(Scholarship(
        name="Quick STEM Scholarship",
        no_essay_quick_apply=True,
        application_url="https://bold.org/scholarships/quick-stem",
        source_url="https://bold.org/scholarships/quick-stem",
    ))
    assert queue_status(item, Recommendation.QUICK_APPLY) == ScholarshipStatus.QUICK_APPLY


def test_loan_link_is_not_a_scholarship_application() -> None:
    item = classify_candidate(Scholarship(
        name="The University of Texas at Arlington",
        no_essay_quick_apply=True,
        application_url="https://lender.test/refinance-student-loans",
        source_url="https://directory.test/uta-scholarships",
    ))
    assert item.candidate_type == CandidateType.NOT_A_SCHOLARSHIP
    assert queue_status(item, Recommendation.QUICK_APPLY) == ScholarshipStatus.JUNK_RESEARCH


def test_packet_generated_without_prompt(tmp_path: Path) -> None:
    item = ScholarshipRecord.model_validate({
        **classify_candidate(Scholarship(id=1, name="Direct Award", amount=1000, deadline="2027-01-01", eligibility=["Undergraduates"], application_url="https://bold.org/scholarships/direct-award")).model_dump(),
        "status": ScholarshipStatus.APPLY_NOW,
        "ranking": None,
    })
    path = export_application_packet(item, [], tmp_path / "application_packets")
    assert path.is_file()
    assert "No essay prompt found on public page." in path.read_text(encoding="utf-8")


def _ranking(score: float = 70) -> RankingResult:
    return RankingResult(total_score=score, recommendation=Recommendation.MAYBE, explanation=[], breakdown=ScoreBreakdown(fit=70, effort=70, urgency=70, amount=70, competition=70))


def test_apply_now_tolerates_missing_amount_and_deadline() -> None:
    item = classify_candidate(Scholarship(name="Direct STEM Award", eligibility=["Undergraduate STEM students"], application_url="https://bold.org/scholarships/direct-stem"))
    assert item.amount is None and item.deadline is None
    assert queue_status(item, _ranking(65)) == ScholarshipStatus.APPLY_NOW


def test_hard_requirement_still_blocks_apply_now() -> None:
    item = classify_candidate(Scholarship(name="Direct STEM Award", eligibility=["Undergraduate STEM students"], application_url="https://bold.org/scholarships/direct-stem", fafsa_required=True))
    assert queue_status(item, _ranking(80)) != ScholarshipStatus.APPLY_NOW


def test_manual_status_overrides_queue() -> None:
    item = classify_candidate(Scholarship(name="Review Award", user_status=UserStatus.APPROVED_FOR_APPLY))
    assert queue_status(item, _ranking(10)) == ScholarshipStatus.APPLY_NOW
