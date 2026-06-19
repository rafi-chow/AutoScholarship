from datetime import datetime

from src.db import ScholarshipDatabase
from src.discovery import DiscoveryResult, DiscoveryStats
from src import scheduler


def test_scheduler_run_once_without_dashboard(monkeypatch, tmp_path) -> None:
    database = ScholarshipDatabase(tmp_path / "scheduler.db")
    database.initialize()
    monkeypatch.setattr(scheduler, "_database", lambda: database)
    monkeypatch.setattr(scheduler, "DEFAULT_EXPORT_DIR", tmp_path / "exports")

    def fake_discovery(db, profile):
        output = tmp_path / "exports"
        output.mkdir(parents=True, exist_ok=True)
        (output / "latest_discovery_summary.md").write_text("# Summary", encoding="utf-8")
        return DiscoveryResult(
            started_at=datetime(2026, 6, 19),
            finished_at=datetime(2026, 6, 19),
            stats=DiscoveryStats(),
            search_status="disabled",
        )

    monkeypatch.setattr(scheduler, "run_discovery", fake_discovery)
    monkeypatch.setattr(scheduler, "send_optional_email", lambda *args, **kwargs: "Email skipped.")

    assert scheduler.main(["run-once"]) == 0
    assert (tmp_path / "exports" / "weekly_action_list.md").is_file()

