"""Load and validate the reusable local scholarship profile."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from src.models import Profile


class ProfileLoadError(ValueError):
    """Raised when a profile file cannot be read or validated."""


def _resolve_document_paths(raw: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    documents = raw.get("documents")
    if not isinstance(documents, dict):
        return raw

    def resolve(value: str | Path | None) -> str | None:
        if value in (None, ""):
            return None
        path = Path(value).expanduser()
        return str(path if path.is_absolute() else (base_dir / path).resolve())

    for key in ("resume", "transcript", "financial_aid_letter"):
        documents[key] = resolve(documents.get(key))
    documents["recommendation_letters"] = [
        resolve(path) for path in documents.get("recommendation_letters", [])
    ]
    documents["other"] = {
        name: resolve(path) for name, path in documents.get("other", {}).items()
    }
    return raw


def load_profile(path: str | Path) -> Profile:
    """Read YAML, resolve document paths relative to it, and validate fields."""

    profile_path = Path(path).expanduser().resolve()
    if not profile_path.is_file():
        raise ProfileLoadError(f"Profile file not found: {profile_path}")
    try:
        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileLoadError(f"Could not read profile: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileLoadError("Profile YAML must contain a top-level mapping.")
    try:
        return Profile.model_validate(_resolve_document_paths(raw, profile_path.parent))
    except ValidationError as exc:
        raise ProfileLoadError(f"Invalid profile:\n{exc}") from exc

