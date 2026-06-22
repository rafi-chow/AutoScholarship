"""One-command discovery, ranking, drafting, queueing, and export workflow."""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from pydantic import Field

from src.db import ScholarshipDatabase
from src.discovery import DiscoveryResult, run_discovery, write_discovery_diagnostics
from src.drafter import generate_and_save_draft
from src.export import (
    DEFAULT_EXPORT_DIR,
    export_approval_queue,
    export_application_packet,
    export_quick_apply_queue_markdown,
    export_weekly_action_list,
)
from src.models import DraftRecord, Profile, Recommendation, ScholarshipRecord, ScholarshipStatus, StrictModel
from src.profile import load_profile
from src.quality import retriage_all
from src.llm import build_llm


ROOT = Path(__file__).resolve().parents[1]


class AutopilotStats(StrictModel):
    found: int = 0
    new: int = 0
    duplicates: int = 0
    drafts_generated: int = 0
    quick_apply_ready: int = 0
    apply_now_ready: int = 0
    blocked_manual: int = 0
    errors: int = 0
    prompts_found: int = 0
    prompts_not_found: int = 0
    packets_generated: int = 0
    drafts_skipped_no_prompt: int = 0
    drafts_skipped_sensitive_input: int = 0
    drafts_failed_llm: int = 0


class AutopilotResult(StrictModel):
    started_at: datetime
    finished_at: datetime
    stats: AutopilotStats
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    new_scholarship_ids: list[int] = Field(default_factory=list)
    draft_ids: list[int] = Field(default_factory=list)
    summary_path: Path


def _write_summary(result: AutopilotResult, database: ScholarshipDatabase, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = (output_dir / "latest_autopilot_summary.md").resolve()
    records = [
        record for item_id in result.new_scholarship_ids
        if (record := database.get_scholarship(item_id)) is not None
    ]
    lines = [
        "# Latest Autopilot Summary", "",
        f"- Started: {result.started_at.isoformat()}",
        f"- Finished: {result.finished_at.isoformat()}",
        f"- Scholarships found: {result.stats.found}",
        f"- New scholarships: {result.stats.new}",
        f"- Duplicates: {result.stats.duplicates}",
        f"- Drafts generated: {result.stats.drafts_generated}",
        f"- Prompts found: {result.stats.prompts_found}",
        f"- Prompts not found: {result.stats.prompts_not_found}",
        f"- Application packets generated: {result.stats.packets_generated}",
        f"- Drafts skipped because no prompt: {result.stats.drafts_skipped_no_prompt}",
        f"- Drafts skipped because sensitive user input is missing: {result.stats.drafts_skipped_sensitive_input}",
        f"- Drafts failed due to LLM error: {result.stats.drafts_failed_llm}",
        f"- Quick Apply ready: {result.stats.quick_apply_ready}",
        f"- Apply Now ready: {result.stats.apply_now_ready}",
        f"- Blocked/manual items: {result.stats.blocked_manual}",
        f"- Errors: {result.stats.errors}", "", "## Newly queued", "",
    ]
    if records:
        for record in records:
            recommendation = record.ranking.recommendation.value if record.ranking else "Unranked"
            lines.append(f"- {record.name} — {recommendation} — queue: {record.status.value.replace('_', ' ').title()}")
    else:
        lines.append("- None this run.")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {value}" for value in result.warnings or ["None."])
    lines.extend(["", "## Errors", ""])
    lines.extend(f"- {value}" for value in result.errors or ["None."])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_autopilot(
    database: ScholarshipDatabase,
    profile: Profile,
    *,
    output_dir: str | Path = DEFAULT_EXPORT_DIR,
    discovery_runner: Callable[..., DiscoveryResult] = run_discovery,
    draft_generator: Callable[..., DraftRecord] = generate_and_save_draft,
) -> AutopilotResult:
    """Run the full local pipeline. Browser actions are deliberately excluded."""

    database.initialize()
    started = datetime.now()
    discovery = discovery_runner(database, profile)
    write_discovery_diagnostics(discovery, output_dir)
    retriage_all(database, profile)
    errors = list(discovery.errors)
    warnings = list(discovery.warnings)
    draft_ids: list[int] = []
    records = database.list_scholarships()
    actionable = [item for item in records if item.status in {
        ScholarshipStatus.APPLY_NOW, ScholarshipStatus.QUICK_APPLY, ScholarshipStatus.MAYBE,
    }]
    output = Path(output_dir)
    packet_dir = output / "application_packets"
    packet_dir.mkdir(parents=True, exist_ok=True)
    for stale_packet in packet_dir.glob("*.md"):
        stale_packet.unlink()
    packet_paths = [export_application_packet(item, database.list_drafts(), packet_dir) for item in actionable]
    prompts_found = sum(len(item.essay_prompts) for item in actionable)
    no_prompt = sum(1 for item in actionable if not item.essay_prompts)

    top_apply = sorted(
        (item for item in records if item.status == ScholarshipStatus.APPLY_NOW and item.essay_prompts),
        key=lambda item: item.ranking.total_score if item.ranking else 0,
        reverse=True,
    )[:10]
    llm = build_llm()
    allow_drafting = llm.enabled or draft_generator is not generate_and_save_draft
    skipped_sensitive = 0
    llm_failures = 0
    if allow_drafting:
        for record in top_apply:
            for prompt in record.essay_prompts:
                lowered = prompt.lower()
                if any(term in lowered for term in ("immigration", "family hardship", "family income", "exact service hours")):
                    skipped_sensitive += 1
                    continue
                try:
                    kwargs = {"database": database}
                    if draft_generator is generate_and_save_draft:
                        kwargs["llm"] = llm
                        kwargs["require_llm"] = True
                    draft = draft_generator(record, prompt, **kwargs)
                    if draft.id is not None:
                        draft_ids.append(draft.id)
                except Exception as exc:
                    llm_failures += 1
                    message = f"{record.name}: LLM draft generation failed safely: {exc}"
                    errors.append(message)

    records = database.list_scholarships()
    drafts = database.list_drafts()
    quick_ready = sum(
        1 for item in records
        if item.status == ScholarshipStatus.QUICK_APPLY
    )
    apply_ready = sum(1 for item in records if item.status == ScholarshipStatus.APPLY_NOW)
    manual_statuses = {
        ScholarshipStatus.MANUAL_REVIEW, ScholarshipStatus.BLOCKED_SOURCE,
        ScholarshipStatus.MAV_MANUAL_CHECK, ScholarshipStatus.NEEDS_EDIT,
    }
    blocked_manual = sum(1 for item in records if item.status in manual_statuses) + discovery.stats.skipped_blocked
    stats = AutopilotStats(
        found=discovery.stats.found,
        new=discovery.stats.new,
        duplicates=discovery.stats.duplicates,
        drafts_generated=len(draft_ids),
        quick_apply_ready=quick_ready,
        apply_now_ready=apply_ready,
        blocked_manual=blocked_manual,
        errors=len(errors),
        prompts_found=prompts_found,
        prompts_not_found=no_prompt,
        packets_generated=len(packet_paths),
        drafts_skipped_no_prompt=no_prompt,
        drafts_skipped_sensitive_input=skipped_sensitive,
        drafts_failed_llm=llm_failures,
    )
    placeholder = (output / "latest_autopilot_summary.md").resolve()
    result = AutopilotResult(
        started_at=started,
        finished_at=datetime.now(),
        stats=stats,
        errors=errors,
        warnings=warnings,
        new_scholarship_ids=discovery.new_scholarship_ids,
        draft_ids=draft_ids,
        summary_path=placeholder,
    )
    result = result.model_copy(update={"summary_path": _write_summary(result, database, output)})
    export_approval_queue(records, drafts, output)
    export_quick_apply_queue_markdown(records, output)
    export_weekly_action_list(records, drafts, output)
    database.save_autopilot_run(result.model_dump(mode="json"))
    return result


def _runtime() -> tuple[ScholarshipDatabase, Profile]:
    load_dotenv(ROOT / ".env")
    configured = Path(os.getenv("SCHOLARSHIP_DB_PATH", "data/scholarships.db"))
    database = ScholarshipDatabase(configured if configured.is_absolute() else ROOT / configured)
    profile_path = ROOT / "data" / "profile.yaml"
    return database, load_profile(profile_path if profile_path.exists() else ROOT / "data" / "profile.example.yaml")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scholarship Copilot one-button pipeline")
    parser.add_argument("command", choices=("run",))
    args = parser.parse_args(argv)
    database, profile = _runtime()
    result = run_autopilot(database, profile)
    print(
        f"Autopilot complete: found={result.stats.found} new={result.stats.new} "
        f"duplicates={result.stats.duplicates} drafts={result.stats.drafts_generated} "
        f"errors={result.stats.errors}"
    )
    print(f"Summary: {result.summary_path}")
    return 0 if result.stats.errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
