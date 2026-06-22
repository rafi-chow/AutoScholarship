"""Secret-safe provider configuration healthcheck."""

from __future__ import annotations

from src.config import load_environment, provider_status


def main() -> int:
    load_environment()
    status = provider_status()
    print(f"LLM provider configured: {'yes' if status['llm_configured'] else 'no'}")
    print(f"LLM model: {status['llm_model']}")
    print(f"OpenAI API key present: {'yes' if status['openai_api_key_present'] else 'no'}")
    print(f"Search provider configured: {'yes' if status['search_configured'] else 'no'}")
    print(f"Tavily key present: {'yes' if status['tavily_api_key_present'] else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

