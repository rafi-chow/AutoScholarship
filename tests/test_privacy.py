import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_private_artifacts_are_gitignored_and_not_tracked() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        ".env", "data/profile.yaml", "data/*.db", "data/browser_profile/",
        "*.pdf", "drafts/*.md", "screenshots/*.png", "autofill_logs/", "exports/",
    ):
        assert pattern in gitignore

    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if inside.returncode == 0:
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
        forbidden = {
            "data/profile.yaml",
            "data/scholarships.db",
            "data/story_bank.md",
            "data/scholarship_answers_bank.md",
            "data/bot_context.md",
        }
        assert forbidden.isdisjoint(tracked)

