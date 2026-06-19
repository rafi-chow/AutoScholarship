"""CLI entry point for discovery and weekly exports without Streamlit."""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from src.db import ScholarshipDatabase
from src.discovery import run_discovery
from src.export import DEFAULT_EXPORT_DIR, export_weekly_action_list
from src.notifications import send_optional_email
from src.profile import load_profile


ROOT = Path(__file__).resolve().parents[1]


def _database() -> ScholarshipDatabase:
    import os

    configured = Path(os.getenv("SCHOLARSHIP_DB_PATH", "data/scholarships.db"))
    return ScholarshipDatabase(configured if configured.is_absolute() else ROOT / configured)


def run_discovery_command(*, notify: bool = True) -> int:
    load_dotenv(ROOT / ".env")
    database = _database()
    database.initialize()
    profile_path = ROOT / "data" / "profile.yaml"
    if not profile_path.exists():
        profile_path = ROOT / "data" / "profile.example.yaml"
    profile = load_profile(profile_path)
    result = run_discovery(database, profile)
    weekly = export_weekly_action_list(
        database.list_scholarships(),
        database.list_drafts(),
        DEFAULT_EXPORT_DIR,
    )
    summary_path = Path(DEFAULT_EXPORT_DIR) / "latest_discovery_summary.md"
    notification = "Email disabled for this command."
    if notify:
        notification = send_optional_email(
            "Scholarship Copilot discovery summary",
            summary_path.read_text(encoding="utf-8"),
        )
    print(
        f"Discovery complete: found={result.stats.found} new={result.stats.new} "
        f"duplicates={result.stats.duplicates} skipped_or_blocked={result.stats.skipped_blocked} "
        f"errors={result.stats.errors}"
    )
    print(f"Summary: {summary_path}")
    print(f"Weekly actions: {weekly}")
    print(notification)
    return 0 if result.stats.errors == 0 else 1


def weekly_action_command() -> int:
    load_dotenv(ROOT / ".env")
    database = _database()
    database.initialize()
    path = export_weekly_action_list(
        database.list_scholarships(),
        database.list_drafts(),
        DEFAULT_EXPORT_DIR,
    )
    print(f"Weekly action list: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local Scholarship Copilot scheduler")
    parser.add_argument(
        "command",
        choices=("run-once", "discover", "weekly-action-list"),
    )
    args = parser.parse_args(argv)
    if args.command in {"run-once", "discover"}:
        return run_discovery_command(notify=args.command == "run-once")
    return weekly_action_command()


if __name__ == "__main__":
    raise SystemExit(main())

