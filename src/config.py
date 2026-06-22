"""Centralized repository-root environment loading and provider diagnostics."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]


def load_environment(path: str | Path | None = None, *, override: bool = False) -> Path:
    env_path = Path(path) if path else ROOT / ".env"
    load_dotenv(env_path, override=override)
    return env_path


def provider_status(env: Mapping[str, str] | None = None) -> dict[str, object]:
    values = env or os.environ
    llm_provider = values.get("LLM_PROVIDER", "none").strip().lower() or "none"
    search_provider = values.get("SEARCH_PROVIDER", "none").strip().lower() or "none"
    openai_present = bool(values.get("OPENAI_API_KEY", "").strip())
    tavily_present = bool((values.get("TAVILY_API_KEY") or values.get("SEARCH_API_KEY", "")).strip())
    return {
        "llm_provider": llm_provider,
        "llm_configured": llm_provider == "openai" and openai_present,
        "llm_model": values.get("LLM_MODEL", "gpt-5-mini").strip() or "gpt-5-mini",
        "openai_api_key_present": openai_present,
        "search_provider": search_provider,
        "search_configured": search_provider == "tavily" and tavily_present,
        "tavily_api_key_present": tavily_present,
    }

