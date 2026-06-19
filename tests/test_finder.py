from datetime import date
from pathlib import Path

from src.db import ScholarshipDatabase
from src.finder import import_manual_text, import_public_url
from src.policy import AccessMode, SourceDefinition
from src.profile import load_profile


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    url = "https://example.org/scholarships/texas-award"
    headers = {"Content-Type": "text/html; charset=utf-8"}
    text = """
        <html><head><title>Texas CS Award</title></head><body>
        <h1>Texas CS Award</h1><p>Amount: $1,000</p>
        <p>Deadline: December 1, 2026</p><p>Texas Computer Science students are eligible.</p>
        </body></html>
    """
    content = text.encode()

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def get(self, url, **kwargs):
        assert url == FakeResponse.url
        assert kwargs["timeout"] == 15
        assert "User-Agent" in kwargs["headers"]
        return FakeResponse()


def test_manual_import_persists_and_ranks(tmp_path: Path) -> None:
    database = ScholarshipDatabase(tmp_path / "manual.db")
    database.initialize()
    profile = load_profile(ROOT / "data" / "profile.example.yaml")

    record = import_manual_text(
        "Name: Texas STEM Award\nAmount: $1000\nDeadline: 2026-12-01",
        database=database,
        profile=profile,
        today=date(2026, 6, 19),
    )

    assert record.id is not None
    assert record.ranking is not None
    assert database.get_scholarship(record.id) == record


def test_public_import_is_policy_checked_persisted_and_ranked(tmp_path: Path) -> None:
    database = ScholarshipDatabase(tmp_path / "public.db")
    database.initialize()
    profile = load_profile(ROOT / "data" / "profile.example.yaml")
    source = SourceDefinition(
        name="Approved example",
        url="https://example.org/scholarships",
        category="cs_stem",
        access_mode=AccessMode.PUBLIC_ALLOWED,
        notes="Approved fixture.",
    )

    record = import_public_url(
        FakeResponse.url,
        source=source,
        database=database,
        profile=profile,
        today=date(2026, 6, 19),
        session=FakeSession(),
    )

    assert record.source_type == "public_url"
    assert record.source_category == "cs_stem"
    assert record.ranking is not None

