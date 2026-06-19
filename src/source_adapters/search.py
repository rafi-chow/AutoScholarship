"""Optional search API provider abstraction with a no-key fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

import requests


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


class SearchProvider:
    name = "none"
    enabled = False
    status = "Search API disabled. Set SEARCH_PROVIDER and SEARCH_API_KEY in .env."

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        return []


class HTTPSearchProvider(SearchProvider):
    def __init__(self, name: str, api_key: str, session: requests.Session | None = None, **config: str) -> None:
        self.name = name
        self.api_key = api_key
        self.session = session or requests.Session()
        self.config = config
        self.enabled = True
        self.status = f"{name} search API configured."

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        if self.name == "serpapi":
            response = self.session.get(
                "https://serpapi.com/search.json",
                params={"q": query, "api_key": self.api_key, "num": max_results},
                timeout=20,
            )
            response.raise_for_status()
            items = response.json().get("organic_results", [])
            return [SearchResult(item.get("title", "Result"), item["link"], item.get("snippet", "")) for item in items[:max_results] if item.get("link")]
        if self.name == "tavily":
            response = self.session.post(
                "https://api.tavily.com/search",
                json={"api_key": self.api_key, "query": query, "max_results": max_results},
                timeout=20,
            )
            response.raise_for_status()
            items = response.json().get("results", [])
            return [SearchResult(item.get("title", "Result"), item["url"], item.get("content", "")) for item in items[:max_results] if item.get("url")]
        if self.name == "bing":
            response = self.session.get(
                "https://api.bing.microsoft.com/v7.0/search",
                params={"q": query, "count": max_results},
                headers={"Ocp-Apim-Subscription-Key": self.api_key},
                timeout=20,
            )
            response.raise_for_status()
            items = response.json().get("webPages", {}).get("value", [])
            return [SearchResult(item.get("name", "Result"), item["url"], item.get("snippet", "")) for item in items[:max_results] if item.get("url")]
        if self.name == "google":
            response = self.session.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"q": query, "key": self.api_key, "cx": self.config["google_cse_id"], "num": min(max_results, 10)},
                timeout=20,
            )
            response.raise_for_status()
            items = response.json().get("items", [])
            return [SearchResult(item.get("title", "Result"), item["link"], item.get("snippet", "")) for item in items if item.get("link")]
        return []


def build_search_provider(
    env: Mapping[str, str] | None = None,
    *,
    session: requests.Session | None = None,
) -> SearchProvider:
    values = env or os.environ
    name = values.get("SEARCH_PROVIDER", "none").strip().lower()
    api_key = values.get("SEARCH_API_KEY", "").strip()
    if name == "none" or not api_key:
        provider = SearchProvider()
        provider.status = "Search API not configured. Add SEARCH_PROVIDER and SEARCH_API_KEY to .env; curated/RSS discovery still works."
        return provider
    if name not in {"google", "serpapi", "tavily", "bing"}:
        provider = SearchProvider()
        provider.status = f"Unsupported SEARCH_PROVIDER={name!r}; use none/google/serpapi/tavily/bing."
        return provider
    if name == "google" and not values.get("GOOGLE_CSE_ID"):
        provider = SearchProvider()
        provider.status = "Google search requires GOOGLE_CSE_ID in addition to SEARCH_API_KEY."
        return provider
    return HTTPSearchProvider(
        name,
        api_key,
        session=session,
        google_cse_id=values.get("GOOGLE_CSE_ID", ""),
    )
