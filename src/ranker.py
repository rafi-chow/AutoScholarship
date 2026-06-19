"""Transparent, deterministic profile-to-scholarship ranking."""

from __future__ import annotations

import math
from datetime import date

from src.models import (
    Profile,
    RankingResult,
    Recommendation,
    Scholarship,
    ScoreBreakdown,
)


WEIGHTS = {"fit": 0.45, "effort": 0.15, "urgency": 0.15, "amount": 0.15, "competition": 0.10}
PRIORITY_TERMS = {
    "university of texas at arlington": 18,
    "uta": 18,
    "texas": 10,
    "dfw": 12,
    "dallas-fort worth": 12,
    "computer science": 14,
    "software": 10,
    "data": 8,
    "automation": 10,
    "stem": 10,
    "engineering": 12,
    "aerospace": 14,
    "bell": 14,
    "undergraduate": 8,
    "local foundation": 8,
    "south asian": 10,
    "bengali": 12,
    "asian": 8,
    "muslim": 10,
    "community": 6,
    "u.s. citizen": 8,
    "us citizen": 8,
}


def _text(scholarship: Scholarship) -> str:
    values = [
        scholarship.name,
        scholarship.provider or "",
        *scholarship.eligibility,
        *scholarship.location_restrictions,
        *scholarship.school_restrictions,
        *scholarship.major_restrictions,
        *scholarship.citizenship_residency_requirements,
    ]
    return " ".join(values).lower()


def _profile_terms(profile: Profile) -> set[str]:
    terms = {profile.address.city.lower(), profile.address.state.lower()}
    for education in profile.education:
        terms.update({education.school.lower(), education.major.lower(), education.degree.lower()})
    terms.update(item.lower() for item in profile.interests)
    terms.update(item.lower() for item in profile.eligibility_preferences.identity_scholarship_preferences)
    terms.update(goal.lower() for goal in profile.career_goals)
    for experience in profile.work_experience:
        terms.update({experience.organization.lower(), experience.title.lower()})
    return {term for term in terms if len(term) > 1}


def _fit_score(profile: Profile, scholarship: Scholarship) -> tuple[float, list[str]]:
    text = _text(scholarship)
    score = 30.0
    reasons: list[str] = []
    matched_priorities: list[str] = []
    for term, boost in PRIORITY_TERMS.items():
        if term in text:
            score += boost
            matched_priorities.append(term.upper() if term == "uta" else term.title())
    direct_matches = sorted(term for term in _profile_terms(profile) if term in text)
    score += min(20, len(direct_matches) * 4)
    if matched_priorities:
        reasons.append("Priority fit: " + ", ".join(matched_priorities[:5]) + ".")
    elif direct_matches:
        reasons.append("Profile match: " + ", ".join(direct_matches[:4]) + ".")
    else:
        reasons.append("No strong profile-specific preference was detected.")
    return min(100.0, score), reasons


def _effort_score(scholarship: Scholarship) -> tuple[float, str]:
    hours = scholarship.effort_hours
    if hours is None:
        hours = 1.0 + 1.25 * len(scholarship.essay_prompts) + 0.2 * len(scholarship.required_documents)
        if scholarship.no_essay_quick_apply:
            hours = min(hours, 0.5)
    score = max(0.0, 100.0 - hours * 12.5)
    return score, f"Estimated effort is {hours:.1f} hour(s)."


def _urgency_score(scholarship: Scholarship, today: date) -> tuple[float, str]:
    if scholarship.deadline is None:
        return 35.0, "Deadline is unknown; verify it before investing significant effort."
    days = (scholarship.deadline - today).days
    if days < 0:
        return 0.0, f"Deadline passed {abs(days)} day(s) ago."
    if days <= 7:
        return 100.0, f"Deadline is urgent: {days} day(s) away."
    if days <= 30:
        return 85.0, f"Deadline is approaching in {days} days."
    if days <= 90:
        return 65.0, f"Deadline is {days} days away."
    return 40.0, f"Deadline is {days} days away."


def _amount_score(amount: float | None) -> tuple[float, str]:
    if amount is None:
        return 35.0, "Award amount is unknown."
    score = min(100.0, 20.0 + 20.0 * math.log10(max(amount, 100) / 100))
    return score, f"Award amount is ${amount:,.0f}."


def _competition_score(level: str | None) -> tuple[float, str]:
    scores = {"low": 90.0, "medium": 60.0, "high": 30.0, None: 50.0}
    label = level or "unknown"
    return scores[level], f"Competition level is {label}."


def _hard_conflicts(profile: Profile, scholarship: Scholarship, today: date) -> list[str]:
    conflicts: list[str] = []
    if scholarship.deadline and scholarship.deadline < today:
        conflicts.append("The application deadline has passed.")

    current_gpas = [education.gpa for education in profile.education if education.gpa is not None]
    eligibility_text = " ".join(scholarship.eligibility).lower()
    # Only flag common explicit GPA phrasings; uncertain language remains for human review.
    import re
    match = re.search(r"(?:minimum|min\.?|at least)\s*(?:gpa\s*(?:of)?\s*)?(\d(?:\.\d+)?)", eligibility_text)
    if match and current_gpas and max(current_gpas) < float(match.group(1)):
        conflicts.append(f"Profile GPA does not meet the stated {match.group(1)} minimum.")
    preferences = profile.eligibility_preferences
    first_generation_required = scholarship.first_generation_required is True or (
        scholarship.first_generation_required is None
        and any(phrase in eligibility_text for phrase in (
            "first-generation students only", "first generation students only",
            "must be first-generation", "must be a first-generation",
        ))
    )
    if preferences.first_generation is False and first_generation_required:
        conflicts.append("Scholarship requires first-generation status, which the profile does not claim.")
    fafsa_required = scholarship.fafsa_required is True or (
        scholarship.fafsa_required is None
        and any(phrase in eligibility_text for phrase in (
            "fafsa required", "must complete the fafsa", "must submit the fafsa",
        ))
    )
    if (
        preferences.fafsa_completed is False
        and fafsa_required
        and "fafsa_required" not in scholarship.manual_overrides
    ):
        conflicts.append("Scholarship requires FAFSA, which the profile marks incomplete.")
    document_text = " ".join(scholarship.required_documents).lower()
    recommendation_required = scholarship.recommendation_required is True or (
        scholarship.recommendation_required is None
        and any(phrase in document_text for phrase in ("recommendation", "reference letter"))
    )
    if (
        profile.scholarship_preferences.skip_recommendation_required
        and recommendation_required
        and "recommendation_required" not in scholarship.manual_overrides
    ):
        conflicts.append("A recommendation is required, and the profile says to skip these by default.")
    minimum_award = profile.scholarship_preferences.minimum_award
    if (
        scholarship.amount is not None
        and scholarship.amount < minimum_award
        and "minimum_award" not in scholarship.manual_overrides
    ):
        conflicts.append(f"Award is below the profile's ${minimum_award:,.0f} minimum.")
    return conflicts


def _eligibility_reasons(profile: Profile, scholarship: Scholarship) -> list[str]:
    text = _text(scholarship)
    reasons: list[str] = []
    if profile.citizenship and any(term in text for term in ("u.s. citizen", "us citizen", "united states citizen")):
        reasons.append("Verified U.S. citizenship matches the stated requirement.")
    if profile.eligibility_preferences.texas_resident and "texas" in text:
        reasons.append("Verified Texas residency matches the stated preference or requirement.")
    identity_matches = [
        identity for identity in profile.eligibility_preferences.identity_scholarship_preferences
        if identity.lower() in text
    ]
    if identity_matches:
        reasons.append("Eligible community fit: " + ", ".join(identity_matches) + ".")
    if scholarship.need_only is True:
        reasons.append("Strict need-only eligibility is a weak profile fit and receives a score penalty.")
    if scholarship.no_essay_quick_apply:
        reasons.append("No-essay/quick-apply opportunity: low effort but likely low expected value.")
    return reasons


def rank_scholarship(
    profile: Profile,
    scholarship: Scholarship,
    *,
    today: date | None = None,
) -> RankingResult:
    """Rank one opportunity and return an auditable explanation."""

    today = today or date.today()
    fit, fit_reasons = _fit_score(profile, scholarship)
    effort, effort_reason = _effort_score(scholarship)
    urgency, urgency_reason = _urgency_score(scholarship, today)
    amount, amount_reason = _amount_score(scholarship.amount)
    competition, competition_reason = _competition_score(scholarship.competition_level)
    breakdown = ScoreBreakdown(
        fit=round(fit, 1), effort=round(effort, 1), urgency=round(urgency, 1),
        amount=round(amount, 1), competition=round(competition, 1),
    )
    total = sum(getattr(breakdown, key) * weight for key, weight in WEIGHTS.items())
    eligibility_reasons = _eligibility_reasons(profile, scholarship)
    if scholarship.need_only is True:
        total = max(0.0, total - 20.0)
    conflicts = _hard_conflicts(profile, scholarship, today)
    if conflicts:
        recommendation = Recommendation.SKIP
        total = min(total, 39.0)
    elif scholarship.no_essay_quick_apply:
        recommendation = Recommendation.QUICK_APPLY
    elif total >= 70:
        recommendation = Recommendation.APPLY
    elif total >= 45:
        recommendation = Recommendation.MAYBE
    else:
        recommendation = Recommendation.SKIP
    return RankingResult(
        scholarship_id=scholarship.id,
        total_score=round(total, 1),
        recommendation=recommendation,
        explanation=[
            *fit_reasons,
            *eligibility_reasons,
            effort_reason,
            urgency_reason,
            amount_reason,
            competition_reason,
        ],
        hard_conflicts=conflicts,
        breakdown=breakdown,
    )
