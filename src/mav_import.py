"""Manual-only Mav ScholarShop opportunity import helpers."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.db import ScholarshipDatabase
from src.finder import import_manual_text
from src.models import Profile, ScholarshipRecord
from src.policy import AccessMode, SourceDefinition, load_source_catalog


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES_PATH = ROOT / "data" / "sources.yaml"


def mav_source(sources_path: str | Path = DEFAULT_SOURCES_PATH) -> SourceDefinition:
    """Return the configured manual-only UTA source, failing closed if misconfigured."""

    catalog = load_source_catalog(sources_path)
    source = next((item for item in catalog.sources if item.category == "uta_manual"), None)
    if source is None:
        raise ValueError("data/sources.yaml has no uta_manual source.")
    if source.access_mode != AccessMode.MANUAL_ONLY:
        raise ValueError("Mav ScholarShop must remain configured as manual_only.")
    return source


def import_mav_opportunity(
    text: str,
    *,
    database: ScholarshipDatabase,
    profile: Profile,
    sources_path: str | Path = DEFAULT_SOURCES_PATH,
    today: date | None = None,
) -> ScholarshipRecord:
    """Parse, rank, and persist text the user copied after logging in manually."""

    return import_manual_text(
        text,
        database=database,
        profile=profile,
        source=mav_source(sources_path),
        today=today,
    )

