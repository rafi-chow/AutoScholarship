from pathlib import Path

from src.policy import (
    AccessMode,
    PolicyAction,
    SourceDefinition,
    check_source_policy,
    load_source_catalog,
    source_matches_url,
)


ROOT = Path(__file__).resolve().parents[1]


def _source(mode: AccessMode) -> SourceDefinition:
    return SourceDefinition(
        name="Test source",
        url="https://example.org/scholarships",
        category="cs_stem",
        access_mode=mode,
        notes="Test policy entry.",
    )


def test_public_source_allows_fetch() -> None:
    decision = check_source_policy(_source(AccessMode.PUBLIC_ALLOWED), PolicyAction.FETCH)

    assert decision.allowed is True
    assert "public_allowed" in decision.reason


def test_manual_source_allows_paste_but_denies_fetch() -> None:
    source = _source(AccessMode.MANUAL_ONLY)

    assert check_source_policy(source, PolicyAction.MANUAL_IMPORT).allowed is True
    denied = check_source_policy(source, PolicyAction.FETCH)
    assert denied.allowed is False
    assert "manual-only" in denied.reason


def test_blocked_source_denies_every_action() -> None:
    source = _source(AccessMode.BLOCKED)

    assert check_source_policy(source, PolicyAction.FETCH).allowed is False
    assert check_source_policy(source, PolicyAction.MANUAL_IMPORT).allowed is False


def test_url_must_stay_in_configured_source_path() -> None:
    source = _source(AccessMode.PUBLIC_ALLOWED)

    assert source_matches_url(source, "https://example.org/scholarships/award-1") is True
    assert source_matches_url(source, "https://example.org/other") is False
    assert source_matches_url(source, "https://evil.example/scholarships") is False


def test_catalog_contains_all_requested_categories() -> None:
    catalog = load_source_catalog(ROOT / "data" / "sources.yaml")
    categories = {source.category for source in catalog.sources}

    assert {
        "uta_manual",
        "texas_local",
        "dfw_local",
        "cs_stem",
        "engineering_aerospace",
        "south_asian_bengali_asian",
        "muslim_community",
        "national_general",
        "no_essay_quick_apply",
    }.issubset(categories)
