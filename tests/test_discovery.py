from datetime import date, datetime
from pathlib import Path

from src.db import ScholarshipDatabase
from src.discovery import (
    DiscoveryResult,
    DiscoveryStats,
    load_search_queries,
    run_discovery,
)
from src.models import Recommendation, ScholarshipStatus
from src.profile import load_profile
from src.source_adapters.search import build_search_provider
from src.policy import load_source_catalog
from src.source_adapters.public import PublicSourceAdapter


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, url: str, text: str, content_type: str = "text/html") -> None:
        self.url = url
        self.text = text
        self.content = text.encode()
        self.headers = {"Content-Type": content_type}
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


class DiscoverySession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url == "https://example.org/robots.txt":
            return FakeResponse(url, "User-agent: *\nAllow: /", "text/plain")
        if url == "https://example.org/scholarships":
            return FakeResponse(
                url,
                '<a href="/scholarships/uta-cs">UTA CS Scholarship Details</a>',
            )
        if url == "https://example.org/scholarships/uta-cs":
            return FakeResponse(
                url,
                """
                <html><head><title>UTA Texas Computer Science Scholarship</title></head><body>
                <h1>UTA Texas Computer Science Scholarship</h1>
                <p>Amount: $2,500</p><p>Deadline: December 1, 2027</p>
                <p>Eligible UTA undergraduate Computer Science and engineering students who are Texas residents.</p>
                <a href="/scholarships/apply">Apply now</a>
                </body></html>
                """,
            )
        raise AssertionError(f"Unexpected URL: {url}")


def _source_file(tmp_path: Path, mode: str = "public_allowed", enabled: bool = True) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "sources.yaml"
    path.write_text(
        f"""
sources:
  - name: Fixture scholarships
    url: https://example.org/scholarships
    category: cs_stem
    access_mode: {mode}
    enabled: {str(enabled).lower()}
    rss_url: null
    notes: Test fixture.
""",
        encoding="utf-8",
    )
    return path


def test_search_query_catalog_loads_profile_queries() -> None:
    catalog = load_search_queries(ROOT / "data" / "search_queries.yaml")

    assert len(catalog.queries) == 17
    assert catalog.queries[0].priority >= catalog.queries[-1].priority
    assert any(item.query == "UTA scholarships computer science" for item in catalog.queries)
    assert all(item.max_results > 0 and item.notes for item in catalog.queries)


def test_no_search_api_key_is_nonfatal() -> None:
    provider = build_search_provider({"SEARCH_PROVIDER": "serpapi", "SEARCH_API_KEY": ""})

    assert provider.enabled is False
    assert provider.search("test", 5) == []
    assert "not configured" in provider.status


def test_public_link_filter_ignores_generic_policy_pages(tmp_path: Path) -> None:
    source = load_source_catalog(_source_file(tmp_path)).sources[0]
    html = """
        <a href="/scholarships">Scholarships</a>
        <a href="/scholarships">Merit-Based Scholarships for Students</a>
        <a href="/scholarships/terms-and-conditions">Scholarship Terms and Conditions</a>
        <a href="/scholarships/competitive-scholarship-non-resident-tuition-waiver">Competitive Scholarship Waiver</a>
        <a href="/scholarships/presidential-scholar">Presidential Scholarship</a>
    """

    links = PublicSourceAdapter.scholarship_links(html, str(source.url), source)

    assert links == ["https://example.org/scholarships/presidential-scholar"]


def test_discovery_mocked_html_extracts_ranks_and_dedupes(tmp_path: Path) -> None:
    database = ScholarshipDatabase(tmp_path / "discovery.db")
    database.initialize()
    profile = load_profile(ROOT / "data" / "profile.example.yaml")
    sources = _source_file(tmp_path)
    session = DiscoverySession()
    env = {"SEARCH_PROVIDER": "none", "SEARCH_API_KEY": ""}

    first = run_discovery(
        database,
        profile,
        sources_path=sources,
        queries_path=ROOT / "data" / "search_queries.yaml",
        session=session,
        env=env,
        exports_dir=tmp_path / "exports",
    )
    second = run_discovery(
        database,
        profile,
        sources_path=sources,
        queries_path=ROOT / "data" / "search_queries.yaml",
        session=DiscoverySession(),
        env=env,
        exports_dir=tmp_path / "exports",
    )

    assert first.stats.found == 1
    assert first.stats.new == 1
    assert second.stats.new == 0
    assert second.stats.duplicates == 1
    records = database.list_scholarships()
    assert len(records) == 1
    assert records[0].ranking is not None
    assert records[0].ranking.recommendation == Recommendation.APPLY
    assert records[0].status == ScholarshipStatus.APPLY_NOW
    assert len(database.get_source_references(records[0].id)) == 1
    assert (tmp_path / "exports" / "latest_discovery_summary.md").is_file()


def test_manual_and_blocked_sources_are_never_fetched(tmp_path: Path) -> None:
    class NoNetworkSession:
        def get(self, *args, **kwargs):
            raise AssertionError("Policy-blocked/manual source must not make a request")

    profile = load_profile(ROOT / "data" / "profile.example.yaml")
    for mode in ("manual_only", "blocked"):
        database = ScholarshipDatabase(tmp_path / f"{mode}.db")
        result = run_discovery(
            database,
            profile,
            sources_path=_source_file(tmp_path / mode, mode=mode),
            queries_path=ROOT / "data" / "search_queries.yaml",
            session=NoNetworkSession(),
            env={"SEARCH_PROVIDER": "none", "SEARCH_API_KEY": ""},
            exports_dir=tmp_path / f"exports-{mode}",
        )
        assert result.stats.found == 0
        assert result.stats.skipped_blocked == 1


def test_discovery_result_model_for_scheduler_fixture() -> None:
    result = DiscoveryResult(
        started_at=datetime(2026, 6, 19),
        finished_at=datetime(2026, 6, 19),
        stats=DiscoveryStats(),
        search_status="disabled",
    )
    assert result.stats.new == 0
