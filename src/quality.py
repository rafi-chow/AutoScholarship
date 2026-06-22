"""Conservative scholarship-page classification and direct-application confidence."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from src.models import CandidateType, Recommendation, RankingResult, Scholarship, ScholarshipStatus, UserStatus
from src.db import ScholarshipDatabase
from src.ranker import rank_scholarship
from src.models import Profile
from src.ease import score_ease


KNOWN_APPLICATION_HOSTS = (
    "academicworks.com", "smapply.io", "applyists.net", "scholarshipamerica.org",
    "bold.org", "goingmerry.com", "scholarshipowl.com", "submittable.com",
)
VIDEO_SOCIAL_NEWS = (
    "youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "instagram.com", "facebook.com",
    "/news/", "press-release", "press_release", "winner-announcement", "scholarship-winners",
    " scholarship winners", "award winners", "scholarship recipients", "announces ", "press release",
)
BLOG_GUIDE = (
    "/blog/", "/article/", "/guide/", "how-to-get-scholarship", "how-to-get-scholarships",
    "tips-and-samples", "study-abroad", "best-scholarship", "top-scholarship",
)
DIRECTORY_TERMS = (
    "scholarship directory", "scholarship opportunities", "scholarships for ",
    "list of scholarships", "find scholarships", "scholarship search", "scholarships and grants",
)
FINANCIAL_AID_TERMS = (
    "financial aid", "admissions", "tuition", "cost of attendance", "degree program",
    "program fees", "paying for college",
)
GRADUATE_TERMS = (
    "graduate only", "graduate students only", "phd", "doctoral", "dissertation",
    "postdoctoral", "graduate fellowship", "master's students only", "masters students only",
)


def _text(item: Scholarship) -> str:
    return " ".join([
        item.name, str(item.source_url or ""), str(item.application_url or ""),
        *item.eligibility, *item.school_restrictions, *item.required_documents,
    ]).lower()


def _other_school_only(item: Scholarship, text: str) -> bool:
    host = urlparse(str(item.source_url or "")).netloc.lower()
    if not host.endswith(".edu") or any(term in host for term in ("uta.edu", "utexas.edu")):
        return False
    national_markers = ("any accredited", "any college", "any university", "nationwide", "national scholarship")
    if any(marker in text for marker in national_markers):
        return False
    enrolled_markers = ("must be enrolled", "currently enrolled", "students at ", "our students", "admitted students")
    return any(marker in text for marker in enrolled_markers) or bool(item.school_restrictions)


def classify_candidate(item: Scholarship) -> Scholarship:
    """Attach auditable type/confidence metadata without discarding the source lead."""

    text = _text(item)
    source = str(item.source_url or "").lower()
    application = str(item.application_url or "").lower()
    name = item.name.lower()
    reasons: list[str] = []
    score = 0.0
    features = (
        (bool(item.name and item.name.lower() not in {"scholarship", "scholarships"}), 15, "Clear scholarship name"),
        (item.amount is not None, 15, "Award amount found"),
        (item.deadline is not None, 15, "Deadline found"),
        (bool(item.eligibility), 15, "Eligibility found"),
        (item.application_url is not None, 20, "Application link found"),
        (any(term in text for term in ("undergraduate", "college student", "bachelor")), 5, "Undergraduate eligibility indicated"),
        (any(term in text for term in ("texas", "dfw", "dallas", "fort worth", "uta", "arlington")), 5, "Texas/DFW/UTA relevance"),
        (any(term in text for term in ("computer science", "software", "stem", "engineering", "aerospace", "data")), 5, "Technical-field relevance"),
        (bool(item.essay_prompts) or item.no_essay_quick_apply, 5, "Prompt or no-essay flag found"),
    )
    for present, points, reason in features:
        if present:
            score += points
            reasons.append(f"+{points}: {reason}")
    if item.recommendation_required is False:
        score = min(100, score + 3); reasons.append("+3: No recommendation required")
    if item.fafsa_required is False:
        score = min(100, score + 3); reasons.append("+3: No FAFSA required")
    if item.first_generation_required is False:
        score = min(100, score + 2); reasons.append("+2: No first-generation requirement")

    if any(term in application for term in ("studentloan", "student-loan", "refinance-student", "private-loan")):
        kind = CandidateType.NOT_A_SCHOLARSHIP
        score = min(score, 25)
        reasons.append("Application link resolves to a loan/refinancing product, not a scholarship application")
    elif any(term in source or term in name for term in VIDEO_SOCIAL_NEWS):
        kind = CandidateType.VIDEO_SOCIAL_NEWS
    elif any(term in " ".join([name, *item.eligibility]).lower() for term in GRADUATE_TERMS) or re.search(r"\bfellowship\b", name):
        kind = CandidateType.GRADUATE_ONLY
    elif _other_school_only(item, text):
        kind = CandidateType.OTHER_SCHOOL_ONLY
    elif any(term in source or term in name for term in BLOG_GUIDE):
        kind = CandidateType.BLOG_ARTICLE_GUIDE
    elif any(term in name for term in DIRECTORY_TERMS) or re.match(r"^\d+\s+.*scholarships?\b", name) or name in {"grants and scholarships", "scholarships and grants"} or name.startswith((
        "scholarships ", "scholarships for", "best ", "top ", "monthly scholarships", "current scholarships",
    )):
        kind = CandidateType.DIRECTORY_LIST
    elif any(term in name for term in FINANCIAL_AID_TERMS) and item.amount is None and not item.application_url:
        kind = CandidateType.FINANCIAL_AID_ADMISSIONS
    elif "scholar" not in text and "award" not in text and not item.application_url:
        kind = CandidateType.NOT_A_SCHOLARSHIP
    elif item.application_url and any(host in str(item.application_url) for host in KNOWN_APPLICATION_HOSTS):
        kind = CandidateType.DIRECT_APPLICATION
    elif item.application_url and score >= 60:
        kind = CandidateType.DIRECT_APPLICATION
    elif score >= 55 and any(term in name for term in ("scholarship", "award")):
        kind = CandidateType.DETAIL_PAGE
    elif not item.amount and not item.deadline and not item.eligibility and not item.application_url:
        kind = CandidateType.NOT_A_SCHOLARSHIP
    else:
        kind = CandidateType.UNKNOWN_REVIEW
    reasons.append(f"Classified as {kind.value}")
    return item.model_copy(update={"candidate_type": kind, "confidence_score": min(score, 100), "confidence_reasons": reasons})


def why_not_apply_now(item: Scholarship, recommendation: Recommendation, score: float = 0) -> list[str]:
    reasons: list[str] = []
    if item.deadline is None: reasons.append("Missing deadline (warning only)")
    if item.amount is None: reasons.append("Missing amount (warning only)")
    if item.application_url is None: reasons.append("Missing application URL / no direct apply link")
    if not item.eligibility: reasons.append("Eligibility uncertain")
    if item.candidate_type == CandidateType.OTHER_SCHOOL_ONLY: reasons.append("Other-school-only risk")
    if item.candidate_type == CandidateType.DIRECTORY_LIST: reasons.append("Directory/list page")
    if item.confidence_score < 55: reasons.append("Low-confidence extraction")
    if item.recommendation_required is None: reasons.append("Recommendation requirement unknown")
    if item.fafsa_required is None: reasons.append("FAFSA requirement unknown")
    if item.need_only is None: reasons.append("Need-only eligibility unclear")
    if recommendation not in {Recommendation.APPLY, Recommendation.QUICK_APPLY} and score < 60:
        reasons.append("Ranking score below automatic Apply Now threshold")
    if not reasons: reasons.append("User review required")
    return reasons


def queue_status(item: Scholarship, ranking: Recommendation | RankingResult) -> ScholarshipStatus:
    recommendation = ranking.recommendation if isinstance(ranking, RankingResult) else ranking
    score = ranking.total_score if isinstance(ranking, RankingResult) else (100 if recommendation == Recommendation.APPLY else 0)
    if item.user_status == UserStatus.APPROVED_FOR_APPLY:
        return ScholarshipStatus.APPLY_NOW
    if item.user_status == UserStatus.QUICK_APPLY:
        return ScholarshipStatus.QUICK_APPLY
    if item.user_status == UserStatus.REJECTED:
        return ScholarshipStatus.SKIPPED
    if item.user_status == UserStatus.JUNK:
        return ScholarshipStatus.JUNK_RESEARCH
    if item.user_status == UserStatus.NEEDS_MORE_INFO:
        return ScholarshipStatus.MANUAL_REVIEW
    if item.candidate_type in {CandidateType.OTHER_SCHOOL_ONLY, CandidateType.GRADUATE_ONLY}:
        return ScholarshipStatus.SKIPPED
    if item.candidate_type in {
        CandidateType.DIRECTORY_LIST, CandidateType.FINANCIAL_AID_ADMISSIONS,
        CandidateType.BLOG_ARTICLE_GUIDE, CandidateType.VIDEO_SOCIAL_NEWS,
        CandidateType.NOT_A_SCHOLARSHIP,
    }:
        return ScholarshipStatus.JUNK_RESEARCH
    if recommendation == Recommendation.SKIP:
        return ScholarshipStatus.SKIPPED
    actionable = item.candidate_type in {CandidateType.DIRECT_APPLICATION, CandidateType.DETAIL_PAGE}
    if recommendation == Recommendation.QUICK_APPLY:
        return ScholarshipStatus.QUICK_APPLY if actionable and item.application_url else ScholarshipStatus.MANUAL_REVIEW
    hard_requirement = any(value is True for value in (
        item.first_generation_required, item.fafsa_required, item.recommendation_required, item.need_only,
    ))
    auto_apply = actionable and item.application_url and item.confidence_score >= 55 and score >= 60 and not hard_requirement
    if recommendation == Recommendation.APPLY or auto_apply:
        return ScholarshipStatus.APPLY_NOW if auto_apply else ScholarshipStatus.MANUAL_REVIEW
    if recommendation == Recommendation.MAYBE:
        return ScholarshipStatus.MAYBE if actionable else ScholarshipStatus.MANUAL_REVIEW
    return ScholarshipStatus.MANUAL_REVIEW


def retriage_all(database: ScholarshipDatabase, profile: Profile) -> list[Scholarship]:
    """Backfill quality metadata and queues for old and newly discovered records."""

    updated: list[Scholarship] = []
    for record in database.list_scholarships():
        base = Scholarship.model_validate(record.model_dump(exclude={"ranking"}))
        classified = classify_candidate(base)
        ranking = record.ranking or rank_scholarship(profile, classified)
        reasons = [] if queue_status(classified, ranking) == ScholarshipStatus.APPLY_NOW else why_not_apply_now(classified, ranking.recommendation, ranking.total_score)
        classified = classified.model_copy(update={"why_not_apply_now": reasons})
        ease_score, ease_reasons, ease_blockers, estimated_time = score_ease(classified, database.list_drafts())
        classified = classified.model_copy(update={"ease_score": ease_score, "ease_reasons": ease_reasons, "ease_blockers": ease_blockers, "estimated_time": estimated_time})
        database.update_scholarship(classified)
        if record.ranking is None:
            database.save_ranking(ranking)
        database.update_scholarship_status(classified.id, queue_status(classified, ranking).value)
        updated.append(classified)
    return updated
