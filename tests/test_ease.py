from src.ease import score_ease
from src.models import Scholarship


def test_no_essay_direct_application_ranks_as_easiest() -> None:
    quick = Scholarship(name="Quick", no_essay_quick_apply=True, application_url="https://bold.org/apply")
    heavy = Scholarship(name="Heavy", essay_prompts=["Write a long essay"], recommendation_required=True, fafsa_required=True, first_generation_required=True, required_documents=["Transcript"])
    assert score_ease(quick)[0] > score_ease(heavy)[0]


def test_fafsa_recommendation_and_first_gen_lower_ease() -> None:
    clear = Scholarship(name="Clear", application_url="https://example.org/apply")
    blocked = clear.model_copy(update={"fafsa_required": True, "recommendation_required": True, "first_generation_required": True})
    assert score_ease(blocked)[0] < score_ease(clear)[0]
