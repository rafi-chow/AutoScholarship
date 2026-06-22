from datetime import date
from pathlib import Path

import pytest

from src.db import ScholarshipDatabase
from src.drafter import generate_and_save_draft, select_story_angle
from src.models import DraftSource, DraftStatus, Scholarship
from src.profile import load_profile


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def _saved_scholarship(tmp_path: Path, **updates) -> tuple[ScholarshipDatabase, Scholarship]:
    database = ScholarshipDatabase(tmp_path / "drafts.db")
    database.initialize()
    scholarship = Scholarship(
        name="Texas Engineering Future Scholarship",
        provider="Example Foundation",
        amount=2000,
        deadline=date(2026, 12, 1),
        essay_prompts=["What are your career goals?"],
    ).model_copy(update=updates)
    scholarship_id = database.add_scholarship(scholarship)
    return database, scholarship.model_copy(update={"id": scholarship_id})


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("What are your computer science career goals?", "career_goals"),
        ("Describe your leadership experience.", "leadership"),
        ("Describe an innovative technical project.", "innovation_technical"),
        ("How have you served your community?", "service"),
        ("How have teaching and communication shaped you?", "communication_teaching"),
        ("How have discipline and perseverance shaped you?", "discipline"),
        ("Describe your research experience.", "research"),
        ("Describe your backend API work with Flask.", "backend_software"),
    ],
)
def test_story_selection(prompt: str, expected: str) -> None:
    assert select_story_angle(prompt) == expected


def test_draft_contains_required_sections_and_safe_verified_story(tmp_path: Path) -> None:
    database, scholarship = _saved_scholarship(tmp_path)

    draft = generate_and_save_draft(
        scholarship,
        "What are your computer science career goals?",
        database=database,
        data_dir=DATA_DIR,
        output_root=tmp_path / "draft-output",
    )
    markdown = draft.path.read_text(encoding="utf-8")

    assert draft.path == (
        tmp_path
        / "draft-output"
        / "texas-engineering-future-scholarship"
        / "what-are-your-computer-science-career-goals.md"
    ).resolve()
    for heading in (
        "## Draft answer",
        "## Shorter version",
        "## Longer version",
        "## Facts used",
        "## Claims to verify",
        "## Missing user input",
        "## Why this angle fits the scholarship",
    ):
        assert heading in markdown
    assert "Bell Textron" in markdown
    assert draft.facts_used
    assert database.get_draft(draft.id) == draft


def test_generated_draft_does_not_make_forbidden_claims(tmp_path: Path) -> None:
    database, scholarship = _saved_scholarship(tmp_path)
    draft = generate_and_save_draft(
        scholarship,
        "Describe your service and how financial support would help your education.",
        database=database,
        data_dir=DATA_DIR,
        output_root=tmp_path / "draft-output",
    )
    markdown = draft.path.read_text(encoding="utf-8").lower()

    forbidden_claims = (
        "i am low-income",
        "my family is low-income",
        "i completed the fafsa",
        "i am first-generation",
        "my recommendation is available",
        "internal hostname",
        "agent id",
    )
    assert not any(claim in markdown for claim in forbidden_claims)


def test_family_hardship_prompt_uses_required_placeholder(tmp_path: Path) -> None:
    database, scholarship = _saved_scholarship(tmp_path)

    draft = generate_and_save_draft(
        scholarship,
        "Explain a family hardship or immigration challenge you have overcome.",
        database=database,
        data_dir=DATA_DIR,
        output_root=tmp_path / "draft-output",
    )
    markdown = draft.path.read_text(encoding="utf-8")

    assert "[NEEDS USER INPUT: exact family hardship / immigration context]" in markdown
    assert draft.status == DraftStatus.NEEDS_USER_INPUT
    assert draft.missing_user_input


def test_financial_need_uses_cautious_language_and_marks_fafsa_missing(tmp_path: Path) -> None:
    database, scholarship = _saved_scholarship(tmp_path, fafsa_required=True)

    draft = generate_and_save_draft(
        scholarship,
        "How would this scholarship address your financial need and FAFSA/SAI situation?",
        database=database,
        data_dir=DATA_DIR,
        output_root=tmp_path / "draft-output",
    )
    markdown = draft.path.read_text(encoding="utf-8")

    assert "reduce education-cost pressure" in markdown
    assert "coursework, technical projects, and professional growth" in markdown
    assert any("FAFSA/SAI information is unavailable" in item for item in draft.missing_user_input)
    assert draft.status == DraftStatus.NEEDS_USER_INPUT


def test_draft_review_status_can_be_updated(tmp_path: Path) -> None:
    database, scholarship = _saved_scholarship(tmp_path)
    draft = generate_and_save_draft(
        scholarship,
        "Describe your leadership experience.",
        database=database,
        data_dir=DATA_DIR,
        output_root=tmp_path / "draft-output",
    )

    updated = database.update_draft_status(draft.id, DraftStatus.READY_TO_REVIEW)

    assert updated.status == DraftStatus.READY_TO_REVIEW
    assert database.list_drafts(DraftStatus.READY_TO_REVIEW) == [updated]


def test_ai_and_fallback_draft_sources_are_persisted(tmp_path: Path) -> None:
    class FakeAI:
        enabled = True
        def generate(self, prompt): return "A grounded AI-generated answer about verified software goals."
    database, scholarship = _saved_scholarship(tmp_path)
    ai = generate_and_save_draft(scholarship, "What are your career goals?", database=database, data_dir=DATA_DIR, output_root=tmp_path / "draft-output", llm=FakeAI(), require_llm=True)
    assert ai.generation_source == DraftSource.AI
    class NoAI:
        enabled = False
    fallback = generate_and_save_draft(scholarship, "Describe your leadership experience.", database=database, data_dir=DATA_DIR, output_root=tmp_path / "draft-output", llm=NoAI())
    assert fallback.generation_source == DraftSource.TEMPLATE_FALLBACK
    assert fallback.status == DraftStatus.NEEDS_REGENERATION
