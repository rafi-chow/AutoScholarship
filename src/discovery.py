"""Headless scholarship discovery, dedupe, ranking, and provenance workflow."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import requests
import yaml
from pydantic import Field

from src.db import ScholarshipDatabase
from src.export import DEFAULT_EXPORT_DIR
from src.extract import extract_scholarship_html
from src.models import Profile, Recommendation, Scholarship, ScholarshipStatus, StrictModel
from src.policy import AccessMode, SourceCatalog, SourceDefinition, load_source_catalog
from src.ranker import rank_scholarship
from src.source_adapters.base import SafeFetcher
from src.source_adapters.public import PublicSourceAdapter
from src.source_adapters.rss import RSSSourceAdapter
from src.source_adapters.search import SearchProvider, build_search_provider


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES_PATH = ROOT / "data" / "sources.yaml"
DEFAULT_QUERIES_PATH = ROOT / "data" / "search_queries.yaml"


class SearchQuery(StrictModel):
    query: str
    category: str
    priority: int = Field(ge=0, le=10)
    max_results: int = Field(ge=1, le=50)
    notes: str


class SearchQueryCatalog(StrictModel):
    queries: list[SearchQuery] = Field(default_factory=list)


class DiscoveryStats(StrictModel):
    found: int = 0
    new: int = 0
    duplicates: int = 0
    skipped_blocked: int = 0
    errors: int = 0


class DiscoveryResult(StrictModel):
    started_at: datetime
    finished_at: datetime
    stats: DiscoveryStats
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    new_scholarship_ids: list[int] = Field(default_factory=list)
    search_status: str


def load_search_queries(path: str | Path = DEFAULT_QUERIES_PATH) -> SearchQueryCatalog:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {"queries": []}
    catalog = SearchQueryCatalog.model_validate(raw)
    return catalog.model_copy(update={"queries": sorted(catalog.queries, key=lambda item: item.priority, reverse=True)})


def update_source_enabled(path: str | Path, source_name: str, enabled: bool) -> None:
    """Persist an explicit local enable/disable choice in sources.yaml."""

    source_path = Path(path)
    raw = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {"sources": []}
    matched = False
    for source in raw.get("sources", []):
        if source.get("name") == source_name:
            source["enabled"] = enabled
            matched = True
            break
    if not matched:
        raise ValueError(f"Source not found: {source_name}")
    source_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _queue_status(scholarship: Scholarship, recommendation: Recommendation) -> ScholarshipStatus:
    if recommendation == Recommendation.SKIP:
        return ScholarshipStatus.SKIPPED
    if scholarship.deadline is None or scholarship.application_url is None:
        return ScholarshipStatus.MANUAL_REVIEW
    if recommendation == Recommendation.APPLY:
        return ScholarshipStatus.APPLY_NOW
    if recommendation == Recommendation.MAYBE:
        return ScholarshipStatus.MAYBE
    return ScholarshipStatus.NEW


def _persist_candidate(
    scholarship: Scholarship,
    *,
    source_name: str,
    source_url: str,
    database: ScholarshipDatabase,
    profile: Profile,
) -> tuple[int, bool]:
    scholarship_id, is_new = database.add_or_merge_scholarship(
        scholarship,
        source_name=source_name,
        source_url=source_url,
    )
    record = database.get_scholarship(scholarship_id)
    if record is None:  # pragma: no cover
        raise RuntimeError("Discovered scholarship could not be read from SQLite.")
    ranking = rank_scholarship(profile, record)
    database.save_ranking(ranking)
    database.update_scholarship_status(
        scholarship_id,
        _queue_status(record, ranking.recommendation).value,
    )
    return scholarship_id, is_new


def run_discovery(
    database: ScholarshipDatabase,
    profile: Profile,
    *,
    sources_path: str | Path = DEFAULT_SOURCES_PATH,
    queries_path: str | Path = DEFAULT_QUERIES_PATH,
    search_provider: SearchProvider | None = None,
    session: requests.Session | None = None,
    env: dict[str, str] | None = None,
    exports_dir: str | Path = DEFAULT_EXPORT_DIR,
) -> DiscoveryResult:
    """Run configured public, RSS, and optional search discovery without a dashboard."""

    database.initialize()
    started = datetime.now()
    stats = DiscoveryStats()
    errors: list[str] = []
    warnings: list[str] = []
    new_ids: list[int] = []
    catalog: SourceCatalog = load_source_catalog(sources_path)
    queries = load_search_queries(queries_path)
    fetcher = SafeFetcher(session=session)
    public_adapter = PublicSourceAdapter(fetcher)
    rss_adapter = RSSSourceAdapter(fetcher)
    provider = search_provider or build_search_provider(env or os.environ, session=session)

    def persist(items: list[Scholarship], source_name: str, fallback_url: str) -> None:
        nonlocal stats
        for scholarship in items:
            stats = stats.model_copy(update={"found": stats.found + 1})
            try:
                scholarship_id, is_new = _persist_candidate(
                    scholarship,
                    source_name=source_name,
                    source_url=str(scholarship.source_url or fallback_url),
                    database=database,
                    profile=profile,
                )
                if is_new:
                    new_ids.append(scholarship_id)
                    stats = stats.model_copy(update={"new": stats.new + 1})
                else:
                    stats = stats.model_copy(update={"duplicates": stats.duplicates + 1})
            except Exception as exc:
                errors.append(f"{source_name}: failed to persist {scholarship.name}: {exc}")
                stats = stats.model_copy(update={"errors": stats.errors + 1})

    for source in catalog.sources:
        if not source.enabled or source.access_mode != AccessMode.PUBLIC_ALLOWED:
            reason = "disabled" if not source.enabled else source.access_mode.value
            database.update_source_state(
                source.name,
                last_fetched=None,
                last_error=None,
                last_status=reason,
            )
            stats = stats.model_copy(update={"skipped_blocked": stats.skipped_blocked + 1})
            continue
        try:
            items, source_warnings = public_adapter.discover(source)
            warnings.extend(f"{source.name}: {warning}" for warning in source_warnings)
            if source.rss_url:
                rss_items, rss_warnings = rss_adapter.discover(source)
                items.extend(rss_items)
                warnings.extend(f"{source.name} RSS: {warning}" for warning in rss_warnings)
            persist(items, source.name, str(source.url))
            database.update_source_state(
                source.name,
                last_fetched=datetime.now().isoformat(),
                last_error=None,
                last_status="ok" if not source_warnings else "warning",
            )
        except Exception as exc:
            message = f"{source.name}: {exc}"
            errors.append(message)
            stats = stats.model_copy(update={"errors": stats.errors + 1})
            database.update_source_state(
                source.name,
                last_fetched=datetime.now().isoformat(),
                last_error=str(exc),
                last_status="error",
            )

    if provider.enabled:
        for query in queries.queries:
            try:
                results = provider.search(query.query, query.max_results)
            except Exception as exc:
                errors.append(f"Search query {query.query!r}: {exc}")
                stats = stats.model_copy(update={"errors": stats.errors + 1})
                continue
            for result in results:
                landing = fetcher.fetch_unknown_landing(result.url)
                if not landing.allowed or "html" not in landing.content_type:
                    warnings.append(f"Search result {result.url}: {landing.reason or 'non-HTML landing page'}")
                    continue
                try:
                    scholarship = extract_scholarship_html(
                        landing.text,
                        page_url=landing.url,
                        source_category=query.category,
                    )
                    persist([scholarship], f"search:{provider.name}:{query.category}", result.url)
                except ValueError as exc:
                    warnings.append(f"Search result {result.url}: {exc}")
    else:
        warnings.append(provider.status)

    result = DiscoveryResult(
        started_at=started,
        finished_at=datetime.now(),
        stats=stats,
        errors=errors,
        warnings=warnings,
        new_scholarship_ids=list(dict.fromkeys(new_ids)),
        search_status=provider.status,
    )
    database.save_discovery_run(result.model_dump(mode="json"))
    write_discovery_summary(result, database, exports_dir)
    return result


def write_discovery_summary(
    result: DiscoveryResult,
    database: ScholarshipDatabase,
    output_dir: str | Path = DEFAULT_EXPORT_DIR,
) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = (directory / "latest_discovery_summary.md").resolve()
    new_records = [
        record for scholarship_id in result.new_scholarship_ids
        if (record := database.get_scholarship(scholarship_id)) is not None
    ]
    new_records.sort(key=lambda item: item.ranking.total_score if item.ranking else -1, reverse=True)
    lines = [
        "# Latest Discovery Summary",
        "",
        f"- Started: {result.started_at.isoformat()}",
        f"- Finished: {result.finished_at.isoformat()}",
        f"- Found: {result.stats.found}",
        f"- New: {result.stats.new}",
        f"- Duplicates: {result.stats.duplicates}",
        f"- Skipped/blocked sources: {result.stats.skipped_blocked}",
        f"- Errors: {result.stats.errors}",
        f"- Search API: {result.search_status}",
        "",
        "## Top newly found scholarships",
        "",
    ]
    lines.extend(
        f"- {item.name} — {item.ranking.recommendation.value if item.ranking else 'Unranked'} "
        f"({item.ranking.total_score:.1f}/100); queue: {item.status.value.replace('_', ' ').title()}"
        if item.ranking else f"- {item.name} — Unranked; queue: {item.status.value.replace('_', ' ').title()}"
        for item in new_records[:10]
    )
    if not new_records:
        lines.append("- None this run.")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {warning}" for warning in result.warnings or ["None."])
    lines.extend(["", "## Errors", ""])
    lines.extend(f"- {error}" for error in result.errors or ["None."])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
