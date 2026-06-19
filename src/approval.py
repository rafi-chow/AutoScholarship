"""Approval-queue risk evaluation and fail-closed submit eligibility."""

from __future__ import annotations

from src.models import DraftRecord, DraftStatus, Scholarship
from src.policy import PolicyAction, PolicyDecision, SourceDefinition, check_source_policy, source_matches_url


def approval_risk_flags(
    scholarship: Scholarship,
    *,
    source: SourceDefinition | None,
    drafts: list[DraftRecord],
    blockers: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Return every condition that prevents a safe final submission."""

    risks: list[str] = []
    if not scholarship.approved_autofill:
        risks.append("Autofill approval has not been granted.")
    if not scholarship.pre_approved_submit:
        risks.append("Safe-submit approval has not been granted.")
    if scholarship.application_url is None:
        risks.append("Application URL is missing.")
    if source is None:
        risks.append("No configured source policy covers the application URL.")
    else:
        decision = check_source_policy(source, PolicyAction.SUBMIT)
        if not decision.allowed:
            risks.append(decision.reason)
        elif scholarship.application_url and not source_matches_url(source, str(scholarship.application_url)):
            risks.append("Application URL is outside the submit-approved source path.")

    requirement_checks = (
        (scholarship.fafsa_required, "FAFSA requirement is present or unknown."),
        (scholarship.first_generation_required, "First-generation requirement is present or unknown."),
        (scholarship.recommendation_required, "Recommendation requirement is present or unknown."),
        (scholarship.need_only, "Strict need-only requirement is present or unknown."),
    )
    for value, message in requirement_checks:
        if value is not False:
            risks.append(message)

    related = [draft for draft in drafts if draft.scholarship_id == scholarship.id]
    if scholarship.essay_prompts and not related:
        risks.append("One or more essay prompts have no generated draft.")
    for draft in related:
        if draft.missing_user_input:
            risks.append(f"Draft has missing user input: {draft.prompt}")
        if draft.status != DraftStatus.READY_TO_REVIEW:
            risks.append(f"Draft has not been marked ready to review: {draft.prompt}")
        try:
            content = draft.path.read_text(encoding="utf-8")
        except OSError:
            risks.append(f"Draft file is unavailable: {draft.path}")
        else:
            if "[NEEDS USER INPUT:" in content:
                risks.append(f"Draft contains an unresolved user-input placeholder: {draft.prompt}")
            if "[UNSUPPORTED CLAIM" in content.upper():
                risks.append(f"Draft contains an unsupported-claim marker: {draft.prompt}")
    risks.extend(f"Page blocker detected: {blocker}" for blocker in blockers)
    return list(dict.fromkeys(risks))


def check_safe_submit_policy(
    scholarship: Scholarship,
    *,
    source: SourceDefinition | None,
    drafts: list[DraftRecord],
    submit_mode: bool,
    blockers: list[str] | tuple[str, ...] = (),
) -> PolicyDecision:
    if not submit_mode:
        return PolicyDecision(allowed=False, reason="Prepare mode never submits the application.")
    risks = approval_risk_flags(scholarship, source=source, drafts=drafts, blockers=blockers)
    if risks:
        return PolicyDecision(allowed=False, reason="Safe submit blocked: " + " ".join(risks))
    return PolicyDecision(
        allowed=True,
        reason="Explicit submit-approved mode and every stored/page safety condition passed.",
    )
