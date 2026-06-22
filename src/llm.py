"""Optional OpenAI Responses API client with a safe no-key fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

import requests


@dataclass
class LLMClient:
    provider: str = "none"
    model: str = "gpt-5-mini"
    api_key: str = ""
    session: requests.Session | None = None

    @property
    def enabled(self) -> bool:
        return self.provider == "openai" and bool(self.api_key)

    @property
    def status(self) -> str:
        return f"OpenAI configured ({self.model})." if self.enabled else "LLM not configured; template fallback active."

    def generate(self, prompt: str) -> str | None:
        if not self.enabled:
            return None
        response = (self.session or requests.Session()).post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "input": prompt},
            timeout=90,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("output_text"):
            return str(payload["output_text"]).strip()
        parts: list[str] = []
        for output in payload.get("output", []):
            for content in output.get("content", []):
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    parts.append(str(content["text"]))
        return "\n".join(parts).strip() or None


def build_llm(env: Mapping[str, str] | None = None, *, session: requests.Session | None = None) -> LLMClient:
    values = env or os.environ
    provider = values.get("LLM_PROVIDER", "none").strip().lower() or "none"
    if provider not in {"openai", "none"}:
        provider = "none"
    return LLMClient(
        provider=provider,
        model=values.get("LLM_MODEL", "gpt-5-mini").strip() or "gpt-5-mini",
        api_key=values.get("OPENAI_API_KEY", "").strip(),
        session=session,
    )

