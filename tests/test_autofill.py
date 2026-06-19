from pathlib import Path

from src.autofill import (
    AutofillStatus,
    FieldDescriptor,
    autofill_application,
    build_autofill_plan,
    detect_blockers_from_snapshot,
    load_form_mappings,
)
from src.models import Scholarship
from src.policy import AccessMode, SourceDefinition, check_submission_policy
from src.profile import load_profile


ROOT = Path(__file__).resolve().parents[1]


def _source(mode: AccessMode = AccessMode.PUBLIC_ALLOWED) -> SourceDefinition:
    return SourceDefinition(
        name="Approved applications",
        url="https://example.org/apply",
        category="test",
        access_mode=mode,
        notes="Test source.",
        allow_submit_automation=mode == AccessMode.PUBLIC_ALLOWED,
    )


def test_common_form_mappings_load() -> None:
    mappings = load_form_mappings(ROOT / "data" / "form_mappings.yaml")

    assert set(mappings.fields) == {
        "first_name", "last_name", "email", "phone", "city", "state", "school",
        "university", "major", "gpa", "graduation_date", "citizenship",
        "texas_resident", "essay", "linkedin", "github",
    }


def test_autofill_plan_only_uses_confident_verified_fields() -> None:
    profile = load_profile(ROOT / "data" / "profile.example.yaml").model_copy(
        update={"email": "student@example.com"}
    )
    mappings = load_form_mappings(ROOT / "data" / "form_mappings.yaml")
    fields = [
        FieldDescriptor(index=0, label="First Name", name="first_name"),
        FieldDescriptor(index=1, label="Email Address", input_type="email"),
        FieldDescriptor(index=2, label="Parent Email Address", input_type="email"),
        FieldDescriptor(index=3, label="Phone Number", input_type="tel"),
        FieldDescriptor(index=4, label="Favorite Color"),
        FieldDescriptor(index=5, label="Password", input_type="password"),
    ]

    plan = build_autofill_plan(fields, mappings, profile)

    assert [(fill.field_index, fill.mapping_name) for fill in plan.fills] == [(0, "first_name"), (1, "email")]
    manual_indexes = {field.field_index for field in plan.manual_fields}
    assert manual_indexes == {2, 3, 4}
    assert all(fill.field_index != 5 for fill in plan.fills)


def test_blocker_detection_catches_login_captcha_2fa_and_protection() -> None:
    blockers = detect_blockers_from_snapshot(
        text="Sign in. Enter verification code. Verify you are human. Access denied.",
        html='<input type="password"><iframe src="recaptcha"></iframe>',
    )

    assert "login required" in blockers
    assert "CAPTCHA/manual human verification" in blockers
    assert "2FA/verification code" in blockers
    assert "paywall or anti-bot protection" in blockers

    sso_login = detect_blockers_from_snapshot(
        text="Sign in with your university account",
        html='<form action="/login"><input autocomplete="username"></form>',
    )
    assert "login required" in sso_login


def test_submit_policy_is_off_by_default_and_requires_every_condition() -> None:
    approved = Scholarship(
        name="Approved",
        application_url="https://example.org/apply/award",
        pre_approved_submit=True,
    )

    assert check_submission_policy(approved, source=_source(), submit_mode=False).allowed is False
    assert check_submission_policy(
        approved.model_copy(update={"pre_approved_submit": False}),
        source=_source(),
        submit_mode=True,
    ).allowed is False
    assert check_submission_policy(approved, source=_source(AccessMode.MANUAL_ONLY), submit_mode=True).allowed is False
    assert check_submission_policy(
        approved,
        source=_source(),
        submit_mode=True,
        blockers=["CAPTCHA"],
    ).allowed is False
    assert check_submission_policy(approved, source=_source(), submit_mode=True).allowed is True


def test_blocked_autofill_never_opens_browser_or_submits(tmp_path: Path) -> None:
    profile = load_profile(ROOT / "data" / "profile.example.yaml")
    scholarship = Scholarship(
        id=10,
        name="Blocked scholarship",
        application_url="https://example.org/apply/blocked",
        pre_approved_submit=True,
    )

    report = autofill_application(
        scholarship,
        profile,
        source=_source(AccessMode.BLOCKED),
        submit_mode=False,
        logs_dir=tmp_path / "logs",
        screenshots_dir=tmp_path / "screenshots",
        browser_profile_dir=tmp_path / "browser",
    )

    assert report.status == AutofillStatus.BLOCKED
    assert report.submit_requested is False
    assert report.submitted is False
    assert report.screenshot_path is None
    assert report.log_path and report.log_path.is_file()
