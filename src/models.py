"""Validated domain models for profiles, scholarships, and rankings."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class StrictModel(BaseModel):
    """Base model that catches misspelled configuration keys."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Address(StrictModel):
    city: str
    state: str = Field(min_length=2, max_length=2)
    country: str = "US"

    @field_validator("state")
    @classmethod
    def uppercase_state(cls, value: str) -> str:
        return value.upper()


class Education(StrictModel):
    school: str
    degree: str
    major: str
    minor: str | None = None
    gpa: float | None = Field(default=None, ge=0, le=4.0)
    graduation_date: date | None = None
    class_level: str | None = None


class Experience(StrictModel):
    organization: str
    title: str
    location: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    current: bool = False
    bullets: list[str] = Field(default_factory=list)


class Activity(StrictModel):
    organization: str
    role: str
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class StoryBlock(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str
    themes: list[str] = Field(default_factory=list)
    situation: str
    action: str
    result: str
    reflection: str | None = None


class Documents(StrictModel):
    resume: Path | None = None
    transcript: Path | None = None
    financial_aid_letter: Path | None = None
    recommendation_letters: list[Path] = Field(default_factory=list)
    other: dict[str, Path] = Field(default_factory=dict)


class Project(StrictModel):
    name: str
    dates: str | None = None
    technologies: list[str] = Field(default_factory=list)
    summary: str


class EligibilityPreferences(StrictModel):
    texas_resident: bool | None = None
    first_generation: bool | None = None
    fafsa_completed: bool | None = None
    household_income_range: str | None = None
    identity_scholarship_preferences: list[str] = Field(default_factory=list)
    family_context_requires_details: bool = True
    exact_service_hours_available: bool = False


class ScholarshipPreferences(StrictModel):
    minimum_award: float = Field(default=0, ge=0)
    skip_recommendation_required: bool = True
    local_first: bool = True
    priority_categories: list[str] = Field(default_factory=list)
    essay_voice: str = "clear, factual, and natural"
    prohibited_claims: list[str] = Field(default_factory=list)


class Profile(StrictModel):
    full_name: str
    preferred_name: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin: str | None = None
    github: str | None = None
    address: Address
    citizenship: str | None = None
    eligibility_preferences: EligibilityPreferences = Field(default_factory=EligibilityPreferences)
    education: list[Education] = Field(min_length=1)
    coursework: list[str] = Field(default_factory=list)
    technical_skills: list[str] = Field(default_factory=list)
    resume_bullets: list[str] = Field(default_factory=list)
    work_experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    activities: list[Activity] = Field(default_factory=list)
    career_goals: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    story_blocks: list[StoryBlock] = Field(default_factory=list)
    documents: Documents = Field(default_factory=Documents)
    scholarship_preferences: ScholarshipPreferences = Field(default_factory=ScholarshipPreferences)


class ScholarshipStatus(StrEnum):
    NEW = "new"
    APPLY_NOW = "apply_now"
    DRAFTS_READY = "drafts_ready"
    NEEDS_DOCUMENTS = "needs_documents"
    SKIPPED = "skipped"
    MAV_MANUAL_CHECK = "mav_manual_check"
    MAYBE = "maybe"
    MANUAL_REVIEW = "manual_review"
    BLOCKED_SOURCE = "blocked_source"
    NEEDS_EDIT = "needs_edit"


class Recommendation(StrEnum):
    APPLY = "Apply"
    MAYBE = "Maybe"
    SKIP = "Skip"
    QUICK_APPLY = "Quick Apply"


class Scholarship(StrictModel):
    id: int | None = None
    name: str
    provider: str | None = None
    amount: float | None = Field(default=None, ge=0)
    deadline: date | None = None
    eligibility: list[str] = Field(default_factory=list)
    location_restrictions: list[str] = Field(default_factory=list)
    school_restrictions: list[str] = Field(default_factory=list)
    major_restrictions: list[str] = Field(default_factory=list)
    essay_prompts: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)
    recommendation_required: bool | None = None
    fafsa_required: bool | None = None
    first_generation_required: bool | None = None
    need_only: bool | None = None
    citizenship_residency_requirements: list[str] = Field(default_factory=list)
    no_essay_quick_apply: bool = False
    manual_overrides: list[str] = Field(default_factory=list)
    application_url: HttpUrl | None = None
    source_url: HttpUrl | None = None
    source_type: str = "manual"
    source_category: str | None = None
    competition_level: str | None = Field(default=None, pattern=r"^(low|medium|high)$")
    effort_hours: float | None = Field(default=None, ge=0)
    status: ScholarshipStatus = ScholarshipStatus.NEW
    pre_approved_submit: bool = False
    approved_autofill: bool = False
    notes: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ScoreBreakdown(StrictModel):
    fit: float = Field(ge=0, le=100)
    effort: float = Field(ge=0, le=100)
    urgency: float = Field(ge=0, le=100)
    amount: float = Field(ge=0, le=100)
    competition: float = Field(ge=0, le=100)


class RankingResult(StrictModel):
    scholarship_id: int | None = None
    total_score: float = Field(ge=0, le=100)
    recommendation: Recommendation
    explanation: list[str]
    hard_conflicts: list[str] = Field(default_factory=list)
    breakdown: ScoreBreakdown
    ranked_at: datetime = Field(default_factory=datetime.now)


class ScholarshipRecord(Scholarship):
    ranking: RankingResult | None = None


class DraftStatus(StrEnum):
    DRAFT = "draft"
    READY_TO_REVIEW = "ready_to_review"
    NEEDS_USER_INPUT = "needs_user_input"


class DraftRecord(StrictModel):
    id: int | None = None
    scholarship_id: int
    scholarship_name: str
    prompt: str
    path: Path
    status: DraftStatus = DraftStatus.DRAFT
    story_angle: str
    facts_used: list[str] = Field(default_factory=list)
    claims_to_verify: list[str] = Field(default_factory=list)
    missing_user_input: list[str] = Field(default_factory=list)
    why_angle_fits: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


def jsonable(value: BaseModel | dict[str, Any]) -> dict[str, Any]:
    """Return JSON-compatible model data for database persistence."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value
