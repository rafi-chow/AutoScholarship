"""Search provider command-line diagnostics."""

from __future__ import annotations

import argparse

from src.config import load_environment, provider_status
from src.discovery import load_search_queries
from src.source_adapters.search import build_search_provider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scholarship search diagnostics")
    parser.add_argument("command", choices=("diagnose",))
    parser.parse_args(argv)
    load_environment()
    status = provider_status()
    provider = build_search_provider()
    queries = load_search_queries()
    print(f"Provider: {provider.name}")
    print(f"Configured: {'yes' if provider.enabled else 'no'}")
    print(f"Tavily key present: {'yes' if status['tavily_api_key_present'] else 'no'}")
    print(f"Queries loaded: {len(queries.queries)}")
    print(f"Status: {provider.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
