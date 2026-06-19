import csv
from datetime import date
from pathlib import Path

from src.export import (
    build_weekly_action_list,
    export_application_packet,
    export_csv_tracker,
    export_draft_packet,
    export_mav_weekly_checklist,
    export_quick_apply_queue,
    export_weekly_action_list,
)
from src.models import (
    DraftRecord,
    DraftStatus,
    RankingResult,
    Recommendation,
    ScholarshipRecord,
    ScholarshipStatus,
    ScoreBreakdown,
)


def _ranking(score: float, recommendation: Recommendation) -> RankingResult:
    return RankingResult(
        scholarship_id=1,
        total_score=score,
        recommendation=recommendation,
        explanation=["Strong verified profile fit."],
        breakdown=ScoreBreakdown(fit=90, effort=80, urgency=70, amount=60, competition=50),
    )


def _records() -> list[ScholarshipRecord]:
    return [
        ScholarshipRecord(
            id=1,
            name="UTA Engineering Award",
            amount=2000,
            deadline=date(2026, 6, 25),
            required_documents=["Transcript"],
            essay_prompts=["Describe your leadership."],
            application_url="https://example.org/apply/uta",
            status=ScholarshipStatus.NEEDS_DOCUMENTS,
            ranking=_ranking(88, Recommendation.APPLY),
        ),
        ScholarshipRecord(
            id=2,
            name="No Essay Drawing",
            amount=500,
            deadline=date(2026, 12, 1),
            no_essay_quick_apply=True,
            ranking=_ranking(61, Recommendation.QUICK_APPLY).model_copy(update={"scholarship_id": 2}),
        ),
        ScholarshipRecord(
            id=3,
            name="Missing Details Award",
            ranking=_ranking(55, Recommendation.MAYBE).model_copy(update={"scholarship_id": 3}),
        ),
    ]


def _draft(tmp_path: Path) -> DraftRecord:
    path = tmp_path / "draft.md"
    path.write_text("# Draft\n\nVerified draft content.", encoding="utf-8")
    return DraftRecord(
        id=1,
        scholarship_id=1,
        scholarship_name="UTA Engineering Award",
        prompt="Describe your leadership.",
        path=path,
        status=DraftStatus.READY_TO_REVIEW,
        story_angle="Leadership",
        facts_used=["Led 10 interns."],
        missing_user_input=["Confirm word limit."],
        why_angle_fits="Leadership prompt.",
    )


def test_weekly_action_list_contains_all_required_sections(tmp_path: Path) -> None:
    content = build_weekly_action_list(_records(), [_draft(tmp_path)], today=date(2026, 6, 19))

    for heading in (
        "Top 5 high-fit scholarships",
        "Deadlines within 14 days",
        "Quick-apply / no-essay queue",
        "Drafts ready",
        "Applications needing documents",
        "Missing information",
        "Mav ScholarShop reminder",
    ):
        assert heading in content
    assert "UTA Engineering Award" in content
    assert "No Essay Drawing" in content
    assert "Confirm word limit" in content


def test_all_exports_generate_expected_files(tmp_path: Path) -> None:
    records = _records()
    draft = _draft(tmp_path)
    output = tmp_path / "exports"

    paths = [
        export_csv_tracker(records, output),
        export_weekly_action_list(records, [draft], output, today=date(2026, 6, 19)),
        export_application_packet(records[0], [draft], output),
        export_draft_packet([draft], output),
        export_quick_apply_queue(records, output),
        export_mav_weekly_checklist(output),
    ]

    assert all(path.is_file() and path.read_text(encoding="utf-8") for path in paths)
    tracker_rows = list(csv.DictReader(paths[0].open(encoding="utf-8")))
    assert len(tracker_rows) == 3
    assert tracker_rows[0]["recommendation"] == "Apply"
    quick_rows = list(csv.DictReader(paths[4].open(encoding="utf-8")))
    assert [row["name"] for row in quick_rows] == ["No Essay Drawing"]
    assert "Led 10 interns" in paths[2].read_text(encoding="utf-8")
    assert "Submit manually" in paths[5].read_text(encoding="utf-8")

