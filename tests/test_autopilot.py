from datetime import datetime
from pathlib import Path

from src.autopilot import run_autopilot
from src.db import ScholarshipDatabase
from src.discovery import DiscoveryResult, DiscoveryStats
from src.models import (
    DraftRecord,
    Recommendation,
    Scholarship,
    ScholarshipStatus,
    ScoreBreakdown,
    RankingResult,
)
from src.profile import load_profile


ROOT = Path(__file__).resolve().parents[1]


def _ranking(item_id: int, recommendation: Recommendation, score: float) -> RankingResult:
    return RankingResult(
        scholarship_id=item_id,
        total_score=score,
        recommendation=recommendation,
        explanation=["Fixture ranking."],
        breakdown=ScoreBreakdown(fit=80, effort=80, urgency=80, amount=80, competition=80),
    )


def test_autopilot_orders_pipeline_and_generates_apply_drafts(tmp_path: Path) -> None:
    database = ScholarshipDatabase(tmp_path / "autopilot.db")
    database.initialize()
    profile = load_profile(ROOT / "data" / "profile.example.yaml")
    events: list[str] = []

    def discovery_runner(db, loaded_profile):
        events.append("discovery")
        apply_id = db.add_scholarship(
            Scholarship(
                name="Apply Now Engineering Scholarship",
                amount=2000,
                deadline="2027-12-01",
                application_url="https://example.org/apply/engineering",
                source_url="https://example.org/scholarships/engineering",
                essay_prompts=["Describe your engineering career goals."],
                recommendation_required=False,
                fafsa_required=False,
                first_generation_required=False,
                need_only=False,
                status=ScholarshipStatus.APPLY_NOW,
            )
        )
        db.save_ranking(_ranking(apply_id, Recommendation.APPLY, 88))
        quick_id = db.add_scholarship(
                Scholarship(
                    name="No Essay Quick Drawing",
                    amount=500,
                    deadline="2027-11-01",
                    application_url="https://bold.org/scholarships/quick-drawing",
                    source_url="https://bold.org/scholarships/quick-drawing",
                no_essay_quick_apply=True,
                recommendation_required=False,
                fafsa_required=False,
                first_generation_required=False,
                need_only=False,
            )
        )
        db.save_ranking(_ranking(quick_id, Recommendation.QUICK_APPLY, 60))
        events.append("ranking")
        now = datetime.now()
        return DiscoveryResult(
            started_at=now,
            finished_at=now,
            stats=DiscoveryStats(found=2, new=2),
            new_scholarship_ids=[apply_id, quick_id],
            search_status="fixture",
        )

    def draft_generator(record, prompt, *, database):
        events.append(f"draft:{record.name}")
        path = tmp_path / f"draft-{record.id}.md"
        path.write_text("# Draft\n", encoding="utf-8")
        return database.save_draft(
            DraftRecord(
                scholarship_id=record.id,
                scholarship_name=record.name,
                prompt=prompt,
                path=path,
                story_angle="Career goals",
                why_angle_fits="Fixture.",
            )
        )

    result = run_autopilot(
        database,
        profile,
        output_dir=tmp_path / "exports",
        discovery_runner=discovery_runner,
        draft_generator=draft_generator,
    )

    assert events == ["discovery", "ranking", "draft:Apply Now Engineering Scholarship"]
    assert result.stats.drafts_generated == 1
    assert result.stats.quick_apply_ready == 1
    assert result.stats.apply_now_ready == 1
    assert result.summary_path.is_file()
    assert (tmp_path / "exports" / "approval_queue.md").is_file()
    quick_export = (tmp_path / "exports" / "quick_apply_queue.md").read_text(encoding="utf-8")
    assert "No Essay Quick Drawing" in quick_export
    assert database.latest_autopilot_run() is not None
