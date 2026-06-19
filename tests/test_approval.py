from pathlib import Path

import pytest

from src.approval import check_safe_submit_policy
from src.models import DraftRecord, DraftStatus, Scholarship
from src.policy import AccessMode, SourceDefinition


def _source(*, submit_allowed: bool = True) -> SourceDefinition:
    return SourceDefinition(
        name="Approved source",
        url="https://example.org/apply",
        category="test",
        access_mode=AccessMode.PUBLIC_ALLOWED,
        notes="Fixture",
        allow_submit_automation=submit_allowed,
    )


def _scholarship(**updates) -> Scholarship:
    base = Scholarship(
        id=1,
        name="Safe fixture",
        application_url="https://example.org/apply/award",
        approved_autofill=True,
        pre_approved_submit=True,
        fafsa_required=False,
        first_generation_required=False,
        recommendation_required=False,
        need_only=False,
    )
    return base.model_copy(update=updates)


def _draft(tmp_path: Path, *, missing: list[str] | None = None) -> DraftRecord:
    path = tmp_path / "draft.md"
    path.write_text("## Draft answer\n\nGrounded answer.\n\n## Facts used\n", encoding="utf-8")
    return DraftRecord(
        id=1,
        scholarship_id=1,
        scholarship_name="Safe fixture",
        prompt="Tell us about your goals.",
        path=path,
        status=DraftStatus.READY_TO_REVIEW,
        story_angle="Career goals",
        missing_user_input=missing or [],
        why_angle_fits="Relevant.",
    )


def test_submit_requires_explicit_approval(tmp_path: Path) -> None:
    decision = check_safe_submit_policy(
        _scholarship(pre_approved_submit=False),
        source=_source(),
        drafts=[_draft(tmp_path)],
        submit_mode=True,
    )
    assert decision.allowed is False
    assert "approval" in decision.reason.lower()


def test_prepare_mode_never_submits(tmp_path: Path) -> None:
    decision = check_safe_submit_policy(
        _scholarship(), source=_source(), drafts=[_draft(tmp_path)], submit_mode=False
    )
    assert decision.allowed is False
    assert "never submits" in decision.reason


def test_submit_blocked_by_missing_input(tmp_path: Path) -> None:
    decision = check_safe_submit_policy(
        _scholarship(),
        source=_source(),
        drafts=[_draft(tmp_path, missing=["Confirm exact detail."])],
        submit_mode=True,
    )
    assert decision.allowed is False
    assert "missing user input" in decision.reason


@pytest.mark.parametrize(
    ("field", "expected"),
    (
        ("fafsa_required", "FAFSA"),
        ("first_generation_required", "First-generation"),
        ("recommendation_required", "Recommendation"),
    ),
)
def test_submit_blocked_by_disallowed_requirements(tmp_path: Path, field: str, expected: str) -> None:
    decision = check_safe_submit_policy(
        _scholarship(**{field: True}),
        source=_source(),
        drafts=[_draft(tmp_path)],
        submit_mode=True,
    )
    assert decision.allowed is False
    assert expected in decision.reason


def test_submit_blocked_when_source_policy_disallows(tmp_path: Path) -> None:
    decision = check_safe_submit_policy(
        _scholarship(),
        source=_source(submit_allowed=False),
        drafts=[_draft(tmp_path)],
        submit_mode=True,
    )
    assert decision.allowed is False
    assert "does not explicitly allow" in decision.reason


def test_submit_allowed_only_when_every_stored_condition_passes(tmp_path: Path) -> None:
    decision = check_safe_submit_policy(
        _scholarship(), source=_source(), drafts=[_draft(tmp_path)], submit_mode=True
    )
    assert decision.allowed is True
