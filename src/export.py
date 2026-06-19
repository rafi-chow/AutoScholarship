"""Local CSV and Markdown exports for review and weekly planning."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from src.drafter import slugify
from src.models import DraftRecord, DraftStatus, Recommendation, ScholarshipRecord, ScholarshipStatus


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPORT_DIR = ROOT / "exports"


def _output_path(output_dir: str | Path, filename: str) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return (directory / filename).resolve()


def _deadline(value: date | None) -> str:
    return value.isoformat() if value else "Unknown"


def _amount(value: float | None) -> str:
    return f"${value:,.0f}" if value is not None else "Unknown"


def export_csv_tracker(
    scholarships: list[ScholarshipRecord],
    output_dir: str | Path = DEFAULT_EXPORT_DIR,
) -> Path:
    path = _output_path(output_dir, "scholarship-tracker.csv")
    fields = (
        "id", "name", "provider", "recommendation", "fit_score", "amount", "deadline",
        "status", "effort_hours", "required_documents", "application_url", "source_url",
        "source_category", "no_essay_quick_apply", "next_action",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in scholarships:
            recommendation = item.ranking.recommendation.value if item.ranking else "Unranked"
            next_action = {
                Recommendation.APPLY.value: "Review and prepare application",
                Recommendation.MAYBE.value: "Verify eligibility and expected value",
                Recommendation.SKIP.value: "No action unless facts change",
                Recommendation.QUICK_APPLY.value: "Complete quick application manually",
                "Unranked": "Rank opportunity",
            }[recommendation]
            writer.writerow({
                "id": item.id,
                "name": item.name,
                "provider": item.provider or "",
                "recommendation": recommendation,
                "fit_score": item.ranking.total_score if item.ranking else "",
                "amount": item.amount if item.amount is not None else "",
                "deadline": _deadline(item.deadline),
                "status": item.status.value,
                "effort_hours": item.effort_hours if item.effort_hours is not None else "",
                "required_documents": "; ".join(item.required_documents),
                "application_url": str(item.application_url or ""),
                "source_url": str(item.source_url or ""),
                "source_category": item.source_category or "",
                "no_essay_quick_apply": item.no_essay_quick_apply,
                "next_action": next_action,
            })
    return path


def build_weekly_action_list(
    scholarships: list[ScholarshipRecord],
    drafts: list[DraftRecord],
    *,
    today: date | None = None,
) -> str:
    today = today or date.today()
    high_fit = sorted(
        (
            item for item in scholarships
            if item.ranking and item.ranking.recommendation != Recommendation.SKIP
        ),
        key=lambda item: item.ranking.total_score,
        reverse=True,
    )[:5]
    urgent = sorted(
        (
            item for item in scholarships
            if item.deadline and 0 <= (item.deadline - today).days <= 14
        ),
        key=lambda item: item.deadline,
    )
    quick = [
        item for item in scholarships
        if item.no_essay_quick_apply
        or (item.ranking and item.ranking.recommendation == Recommendation.QUICK_APPLY)
    ]
    ready = [draft for draft in drafts if draft.status == DraftStatus.READY_TO_REVIEW]
    needs_documents = [item for item in scholarships if item.status == ScholarshipStatus.NEEDS_DOCUMENTS]
    missing_lines: list[str] = []
    for draft in drafts:
        for missing in draft.missing_user_input:
            missing_lines.append(f"{draft.scholarship_name}: {missing}")
    for item in scholarships:
        missing = []
        if item.deadline is None:
            missing.append("deadline")
        if item.amount is None:
            missing.append("award amount")
        if item.application_url is None:
            missing.append("application URL")
        if missing:
            missing_lines.append(f"{item.name}: verify {', '.join(missing)}")

    def scholarship_lines(items: list[ScholarshipRecord]) -> str:
        if not items:
            return "- None currently."
        return "\n".join(
            f"- {item.name} — {item.ranking.total_score:.1f}/100 — {_deadline(item.deadline)} — {_amount(item.amount)}"
            if item.ranking
            else f"- {item.name} — unranked — {_deadline(item.deadline)} — {_amount(item.amount)}"
            for item in items
        )

    draft_lines = (
        "\n".join(f"- {draft.scholarship_name} — {draft.prompt} — `{draft.path}`" for draft in ready)
        if ready else "- None currently."
    )
    document_lines = (
        "\n".join(
            f"- {item.name}: {', '.join(item.required_documents) or 'Review application document list.'}"
            for item in needs_documents
        )
        if needs_documents else "- None currently."
    )
    missing_text = "\n".join(f"- {line}" for line in missing_lines) if missing_lines else "- None currently."
    return f"""# Weekly Scholarship Action List

Generated for {today.isoformat()}.

## Top 5 high-fit scholarships

{scholarship_lines(high_fit)}

## Deadlines within 14 days

{scholarship_lines(urgent)}

## Quick-apply / no-essay queue

{scholarship_lines(quick)}

## Drafts ready

{draft_lines}

## Applications needing documents

{document_lines}

## Missing information

{missing_text}

## Mav ScholarShop reminder

- Log in manually, check Task List / Recommended Opportunities, paste new opportunities into the app, generate drafts, and submit manually after review.
"""


def export_weekly_action_list(
    scholarships: list[ScholarshipRecord],
    drafts: list[DraftRecord],
    output_dir: str | Path = DEFAULT_EXPORT_DIR,
    *,
    today: date | None = None,
) -> Path:
    path = _output_path(output_dir, "weekly_action_list.md")
    path.write_text(build_weekly_action_list(scholarships, drafts, today=today), encoding="utf-8")
    return path


def export_application_packet(
    scholarship: ScholarshipRecord,
    drafts: list[DraftRecord],
    output_dir: str | Path = DEFAULT_EXPORT_DIR,
) -> Path:
    related = [draft for draft in drafts if draft.scholarship_id == scholarship.id]
    ranking = scholarship.ranking
    explanations = "\n".join(f"- {reason}" for reason in ranking.explanation) if ranking else "- Not ranked."
    prompts = "\n".join(f"- {prompt}" for prompt in scholarship.essay_prompts) or "- None extracted."
    documents = "\n".join(f"- [ ] {item}" for item in scholarship.required_documents) or "- None extracted."
    draft_links = "\n".join(f"- {draft.prompt}: `{draft.path}` ({draft.status.value})" for draft in related) or "- None generated."
    draft_details = []
    for draft in related:
        facts = "\n".join(f"- [ ] {fact}" for fact in draft.facts_used) or "- None recorded."
        missing = "\n".join(f"- [ ] {item}" for item in draft.missing_user_input) or "- None recorded."
        content = draft.path.read_text(encoding="utf-8") if draft.path.is_file() else "[Draft file missing.]"
        draft_details.append(
            f"### {draft.prompt}\n\nStatus: {draft.status.value}\n\n"
            f"Facts used:\n\n{facts}\n\nMissing input:\n\n{missing}\n\n{content}"
        )
    draft_detail_text = "\n\n".join(draft_details) or "No draft content generated."
    path = _output_path(output_dir, f"{slugify(scholarship.name, fallback='scholarship')}-application-packet.md")
    path.write_text(
        f"""# {scholarship.name} — Application Packet

## Overview

- Provider: {scholarship.provider or 'Unknown'}
- Amount: {_amount(scholarship.amount)}
- Deadline: {_deadline(scholarship.deadline)}
- Recommendation: {ranking.recommendation.value if ranking else 'Unranked'}
- Fit score: {f'{ranking.total_score:.1f}/100' if ranking else 'Unranked'}
- Application: {str(scholarship.application_url or 'Unknown')}
- Source: {str(scholarship.source_url or 'Unknown')}

## Ranking reasons

{explanations}

## Eligibility

{chr(10).join(f'- {item}' for item in scholarship.eligibility) or '- None extracted.'}

## Required documents

{documents}

## Essay prompts

{prompts}

## Draft files

{draft_links}

## Draft review details

{draft_detail_text}

## Final review

- [ ] Verify eligibility and all factual claims.
- [ ] Confirm deadline and submission instructions on the official page.
- [ ] Review every draft and required document.
- [ ] Submit manually unless every explicit safe-submit condition is satisfied.
""",
        encoding="utf-8",
    )
    return path


def export_draft_packet(
    drafts: list[DraftRecord],
    output_dir: str | Path = DEFAULT_EXPORT_DIR,
) -> Path:
    path = _output_path(output_dir, "all-drafts-packet.md")
    sections = ["# Scholarship Draft Packet", "", "> Every answer remains a draft for human review.", ""]
    if not drafts:
        sections.append("No drafts generated.")
    for draft in drafts:
        sections.extend([
            f"## {draft.scholarship_name}",
            "",
            f"Prompt: {draft.prompt}",
            "",
            f"Status: {draft.status.value}",
            "",
            draft.path.read_text(encoding="utf-8") if draft.path.is_file() else f"[Missing draft file: {draft.path}]",
            "",
        ])
    path.write_text("\n".join(sections), encoding="utf-8")
    return path


def export_quick_apply_queue(
    scholarships: list[ScholarshipRecord],
    output_dir: str | Path = DEFAULT_EXPORT_DIR,
) -> Path:
    path = _output_path(output_dir, "quick-apply-queue.csv")
    quick = [
        item for item in scholarships
        if item.no_essay_quick_apply
        or (item.ranking and item.ranking.recommendation == Recommendation.QUICK_APPLY)
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("name", "amount", "deadline", "application_url", "fit_score", "note"))
        for item in quick:
            writer.writerow((
                item.name,
                item.amount if item.amount is not None else "",
                _deadline(item.deadline),
                str(item.application_url or ""),
                item.ranking.total_score if item.ranking else "",
                "Low effort; verify legitimacy and expected value before applying.",
            ))
    return path


def export_quick_apply_queue_markdown(
    scholarships: list[ScholarshipRecord],
    output_dir: str | Path = DEFAULT_EXPORT_DIR,
) -> Path:
    path = _output_path(output_dir, "quick_apply_queue.md")
    quick = [
        item for item in scholarships
        if item.no_essay_quick_apply
        or (item.ranking and item.ranking.recommendation == Recommendation.QUICK_APPLY)
    ]
    lines = [
        "# Quick Apply Queue",
        "",
        "> Low effort, but usually low expected value. Verify legitimacy and eligibility before proceeding.",
        "",
    ]
    if not quick:
        lines.append("- None currently.")
    for item in sorted(quick, key=lambda value: value.ranking.total_score if value.ranking else 0, reverse=True):
        lines.append(
            f"- **{item.name}** — {_amount(item.amount)} — {_deadline(item.deadline)} — "
            f"{item.ranking.total_score:.1f}/100" if item.ranking else f"- **{item.name}** — unranked"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def export_approval_queue(
    scholarships: list[ScholarshipRecord],
    drafts: list[DraftRecord],
    output_dir: str | Path = DEFAULT_EXPORT_DIR,
) -> Path:
    path = _output_path(output_dir, "approval_queue.md")
    candidates = [
        item for item in scholarships
        if item.ranking and item.ranking.recommendation in {Recommendation.APPLY, Recommendation.QUICK_APPLY}
        and item.status != ScholarshipStatus.SKIPPED
    ]
    candidates.sort(
        key=lambda item: (
            item.ranking.recommendation != Recommendation.QUICK_APPLY,
            -(item.ranking.total_score if item.ranking else 0),
        )
    )
    lines = ["# Scholarship Approval Queue", "", "> Review facts and risks before approving browser actions.", ""]
    if not candidates:
        lines.append("- None currently.")
    for item in candidates:
        related = [draft for draft in drafts if draft.scholarship_id == item.id]
        missing = [value for draft in related for value in draft.missing_user_input]
        claims = [value for draft in related for value in draft.claims_to_verify]
        risks = [
            label for enabled, label in (
                (item.recommendation_required is not False, "Recommendation requirement present/unknown"),
                (item.fafsa_required is not False, "FAFSA requirement present/unknown"),
                (item.first_generation_required is not False, "First-generation requirement present/unknown"),
                (item.need_only is not False, "Need-only requirement present/unknown"),
                (bool(missing), "Draft missing user input"),
                (item.application_url is None, "Missing application URL"),
            ) if enabled
        ]
        lines.extend([
            f"## {item.name}", "",
            f"- Recommendation: {item.ranking.recommendation.value}",
            f"- Fit score: {item.ranking.total_score:.1f}/100",
            f"- Amount: {_amount(item.amount)}",
            f"- Deadline: {_deadline(item.deadline)}",
            f"- Source: {str(item.source_url or 'Unknown')}",
            f"- Autofill approved: {'Yes' if item.approved_autofill else 'No'}",
            f"- Safe submit approved: {'Yes' if item.pre_approved_submit else 'No'}",
            f"- Documents: {', '.join(item.required_documents) or 'None extracted'}",
            f"- Claims to verify: {'; '.join(claims) or 'None recorded'}",
            f"- Missing input: {'; '.join(missing) or 'None recorded'}",
            f"- Risk flags: {'; '.join(risks) or 'No stored-data flags; page checks still required'}",
            "",
        ])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


MAV_WEEKLY_CHECKLIST = """# Mav ScholarShop Weekly Checklist

- [ ] Log into Mav ScholarShop manually.
- [ ] Open Task List / Recommended Opportunities.
- [ ] Copy/paste opportunity text.
- [ ] Import into app.
- [ ] Generate drafts.
- [ ] Submit manually after review.

The app never automates university login or blind submission.
"""


def export_mav_weekly_checklist(output_dir: str | Path = DEFAULT_EXPORT_DIR) -> Path:
    path = _output_path(output_dir, "mav-scholars-shop-weekly-checklist.md")
    path.write_text(MAV_WEEKLY_CHECKLIST, encoding="utf-8")
    return path
