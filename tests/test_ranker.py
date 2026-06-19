from datetime import date
from pathlib import Path

import pytest

from src.models import Recommendation, Scholarship
from src.profile import load_profile
from src.ranker import rank_scholarship


ROOT = Path(__file__).resolve().parents[1]
PROFILE = load_profile(ROOT / "data" / "profile.example.yaml")


def test_local_cs_scholarship_is_high_priority() -> None:
    scholarship = Scholarship(
        name="UTA DFW Computer Science Scholarship",
        provider="Local Foundation",
        amount=2500,
        deadline=date(2026, 8, 1),
        eligibility=["Undergraduate students at the University of Texas at Arlington"],
        location_restrictions=["Texas and DFW residents"],
        major_restrictions=["Computer Science, software, STEM, or engineering"],
        essay_prompts=["Describe your interest in aerospace software."],
        competition_level="low",
        effort_hours=2,
    )

    result = rank_scholarship(PROFILE, scholarship, today=date(2026, 6, 19))

    assert result.recommendation == Recommendation.APPLY
    assert result.breakdown.fit == 100
    assert any("Priority fit" in reason for reason in result.explanation)


def test_expired_scholarship_is_skipped() -> None:
    scholarship = Scholarship(name="Expired award", amount=1000, deadline=date(2026, 1, 1))

    result = rank_scholarship(PROFILE, scholarship, today=date(2026, 6, 19))

    assert result.recommendation == Recommendation.SKIP
    assert "deadline has passed" in result.hard_conflicts[0]


def test_unmet_explicit_gpa_and_recommendation_are_conflicts() -> None:
    scholarship = Scholarship(
        name="Selective Scholarship",
        amount=1000,
        deadline=date(2026, 12, 1),
        eligibility=["Minimum GPA of 3.8"],
        required_documents=["Two recommendation letters"],
    )

    result = rank_scholarship(PROFILE, scholarship, today=date(2026, 6, 19))

    assert result.recommendation == Recommendation.SKIP
    assert len(result.hard_conflicts) == 2


def test_first_generation_requirement_is_not_fabricated() -> None:
    scholarship = Scholarship(
        name="First Generation Award",
        amount=1000,
        eligibility=["Applicants must be first-generation college students"],
    )

    result = rank_scholarship(PROFILE, scholarship, today=date(2026, 6, 19))

    assert result.recommendation == Recommendation.SKIP
    assert any("first-generation" in conflict for conflict in result.hard_conflicts)


def test_structured_first_generation_requirement_is_hard_skip() -> None:
    scholarship = Scholarship(
        name="First Generation Only",
        amount=1000,
        first_generation_required=True,
    )

    result = rank_scholarship(PROFILE, scholarship, today=date(2026, 6, 19))

    assert result.recommendation == Recommendation.SKIP
    assert any("first-generation" in conflict for conflict in result.hard_conflicts)


def test_fafsa_requirement_is_skip_unless_overridden() -> None:
    scholarship = Scholarship(name="FAFSA Award", amount=1000, fafsa_required=True)

    skipped = rank_scholarship(PROFILE, scholarship, today=date(2026, 6, 19))
    overridden = rank_scholarship(
        PROFILE,
        scholarship.model_copy(update={"manual_overrides": ["fafsa_required"]}),
        today=date(2026, 6, 19),
    )

    assert skipped.recommendation == Recommendation.SKIP
    assert any("FAFSA" in conflict for conflict in skipped.hard_conflicts)
    assert not any("FAFSA" in conflict for conflict in overridden.hard_conflicts)


def test_recommendation_requirement_is_skip_unless_overridden() -> None:
    scholarship = Scholarship(name="Reference Award", amount=1000, recommendation_required=True)

    skipped = rank_scholarship(PROFILE, scholarship, today=date(2026, 6, 19))
    overridden = rank_scholarship(
        PROFILE,
        scholarship.model_copy(update={"manual_overrides": ["recommendation_required"]}),
        today=date(2026, 6, 19),
    )

    assert skipped.recommendation == Recommendation.SKIP
    assert any("recommendation" in conflict.lower() for conflict in skipped.hard_conflicts)
    assert not overridden.hard_conflicts


def test_no_essay_lottery_goes_to_quick_apply_queue() -> None:
    scholarship = Scholarship(
        name="Texas No-Essay Drawing",
        amount=500,
        deadline=date(2026, 12, 1),
        no_essay_quick_apply=True,
        competition_level="high",
    )

    result = rank_scholarship(PROFILE, scholarship, today=date(2026, 6, 19))

    assert result.recommendation == Recommendation.QUICK_APPLY
    assert any("low expected value" in reason for reason in result.explanation)


def test_need_only_is_downranked_not_a_hard_conflict() -> None:
    base = Scholarship(
        name="National Scholarship",
        amount=2000,
        deadline=date(2026, 9, 1),
        competition_level="medium",
    )

    general = rank_scholarship(PROFILE, base, today=date(2026, 6, 19))
    need_only = rank_scholarship(
        PROFILE,
        base.model_copy(update={"need_only": True}),
        today=date(2026, 6, 19),
    )

    assert need_only.total_score == pytest.approx(general.total_score - 20)
    assert need_only.hard_conflicts == []
    assert any("score penalty" in reason for reason in need_only.explanation)


def test_citizenship_and_texas_residency_are_explained_positive_matches() -> None:
    scholarship = Scholarship(
        name="Texas Resident Award",
        amount=1000,
        eligibility=["Must be a U.S. citizen and Texas resident"],
        citizenship_residency_requirements=["Must be a U.S. citizen and Texas resident"],
    )

    result = rank_scholarship(PROFILE, scholarship, today=date(2026, 6, 19))

    assert any("citizenship matches" in reason for reason in result.explanation)
    assert any("Texas residency matches" in reason for reason in result.explanation)
