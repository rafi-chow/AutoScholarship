"""Policy-gated manual and public scholarship import workflows."""

from __future__ import annotations

from datetime import date

import requests

from src.db import ScholarshipDatabase
from src.extract import extract_scholarship_html, extract_scholarship_text
from src.models import Profile, Scholarship, ScholarshipRecord
from src.policy import (
    PolicyAction,
    SourceDefinition,
    check_source_policy,
    source_matches_url,
)
from src.ranker import rank_scholarship


USER_AGENT = "ScholarshipCopilot/0.2 (local personal research; policy-gated)"
MAX_PAGE_BYTES = 2_000_000


class SourcePolicyError(PermissionError):
    """Raised when source configuration disallows an import action."""


class SourceFetchError(RuntimeError):
    """Raised when an allowed public page cannot be safely fetched."""


def persist_import(
    scholarship: Scholarship,
    *,
    database: ScholarshipDatabase,
    profile: Profile,
    today: date | None = None,
) -> ScholarshipRecord:
    """Save an extracted scholarship and its current profile ranking."""

    scholarship_id = database.add_scholarship(scholarship)
    saved = scholarship.model_copy(update={"id": scholarship_id})
    database.save_ranking(rank_scholarship(profile, saved, today=today))
    record = database.get_scholarship(scholarship_id)
    if record is None:  # pragma: no cover - defensive database invariant
        raise RuntimeError("Imported scholarship could not be read back from SQLite.")
    return record


def import_manual_text(
    text: str,
    *,
    database: ScholarshipDatabase,
    profile: Profile,
    source: SourceDefinition | None = None,
    today: date | None = None,
) -> ScholarshipRecord:
    """Parse user-pasted text locally and persist it with a ranking."""

    if source is not None:
        decision = check_source_policy(source, PolicyAction.MANUAL_IMPORT)
        if not decision.allowed:
            raise SourcePolicyError(decision.reason)
    scholarship = extract_scholarship_text(
        text,
        source_url=str(source.url) if source else None,
        source_type="manual",
        source_category=source.category if source else None,
    )
    return persist_import(scholarship, database=database, profile=profile, today=today)


def fetch_public_scholarship(
    url: str,
    *,
    source: SourceDefinition,
    timeout: float = 15,
    session: requests.Session | None = None,
) -> Scholarship:
    """Fetch one explicitly allowed public page and extract one opportunity."""

    decision = check_source_policy(source, PolicyAction.FETCH)
    if not decision.allowed:
        raise SourcePolicyError(decision.reason)
    if not source_matches_url(source, url):
        raise SourcePolicyError(
            "Requested URL is outside the configured source URL/path; add and review it in data/sources.yaml first."
        )
    client = session or requests.Session()
    try:
        response = client.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SourceFetchError(f"Could not fetch allowed public source: {exc}") from exc
    if not source_matches_url(source, response.url):
        raise SourcePolicyError("The source redirected outside its configured URL/path; import stopped.")
    content_type = response.headers.get("Content-Type", "").lower()
    if "html" not in content_type:
        raise SourceFetchError(f"Expected an HTML page, received {content_type or 'unknown content type'}.")
    if len(response.content) > MAX_PAGE_BYTES:
        raise SourceFetchError("Page exceeds the 2 MB extraction limit.")
    return extract_scholarship_html(
        response.text,
        page_url=response.url,
        source_category=source.category,
    )


def import_public_url(
    url: str,
    *,
    source: SourceDefinition,
    database: ScholarshipDatabase,
    profile: Profile,
    today: date | None = None,
    session: requests.Session | None = None,
) -> ScholarshipRecord:
    """Policy-check, fetch, extract, persist, and rank a public page."""

    scholarship = fetch_public_scholarship(url, source=source, session=session)
    return persist_import(scholarship, database=database, profile=profile, today=today)

