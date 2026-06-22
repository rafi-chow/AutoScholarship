"""Action-oriented effort scoring for the weekly dashboard."""

from __future__ import annotations

from src.models import CandidateType, DraftRecord, DraftSource, Scholarship


def score_ease(item: Scholarship, drafts: list[DraftRecord] = ()) -> tuple[float, list[str], list[str], str]:
    score = 60.0
    reasons: list[str] = []
    blockers: list[str] = []
    related = [d for d in drafts if d.scholarship_id == item.id]
    ai_ready = any(d.generation_source == DraftSource.AI for d in related)
    if item.no_essay_quick_apply: score += 30; reasons.append("No essay / quick apply")
    elif item.essay_prompts and ai_ready: score += 20; reasons.append("AI draft ready")
    elif len(item.essay_prompts) == 1: score += 8; reasons.append("One prompt found")
    if item.application_url: score += 10; reasons.append("Direct application link available")
    else: score -= 25; blockers.append("Missing application URL")
    penalties = (
        (item.recommendation_required is True, 20, "Recommendation required"),
        (item.fafsa_required is True, 25, "FAFSA required"),
        (item.first_generation_required is True, 30, "First-generation-only requirement"),
        (item.need_only is True, 20, "Strict need-only requirement"),
        (any("transcript" in d.lower() for d in item.required_documents), 10, "Transcript upload required"),
        (not item.eligibility, 12, "Eligibility unclear"),
        (item.deadline is None, 5, "Deadline missing"),
        (item.amount is None, 5, "Amount missing"),
        (len(item.required_documents) > 2, 10, "Several documents required"),
        (item.candidate_type == CandidateType.OTHER_SCHOOL_ONLY, 50, "Other-school-only risk"),
    )
    for present, points, label in penalties:
        if present: score -= points; blockers.append(label)
    score = max(0, min(100, score))
    if score >= 90: estimate = "2 min"
    elif score >= 75: estimate = "5 min"
    elif score >= 60: estimate = "15 min"
    else: estimate = "30+ min"
    return score, reasons, blockers, estimate
