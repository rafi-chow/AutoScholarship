"""Shared safe HTTP and robots checks for discovery adapters."""

from __future__ import annotations

from dataclasses import dataclass
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests

from src.finder import MAX_PAGE_BYTES, USER_AGENT
from src.policy import (
    PolicyAction,
    SourceDefinition,
    check_source_policy,
    check_unknown_public_landing,
    source_matches_url,
)


@dataclass(frozen=True)
class FetchResult:
    allowed: bool
    url: str
    text: str = ""
    content_type: str = ""
    reason: str = ""


class SafeFetcher:
    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: float = 15,
        min_interval_seconds: float = 0.25,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.min_interval_seconds = min_interval_seconds
        self._last_request_at = 0.0
        self._robots: dict[str, bool | None] = {}

    def _get(self, url: str):
        remaining = self.min_interval_seconds - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        response = self.session.get(
            url,
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
        )
        self._last_request_at = time.monotonic()
        return response

    @staticmethod
    def _robots_url(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))

    def robots_allowed(self, url: str) -> bool | None:
        origin = f"{urlsplit(url).scheme}://{urlsplit(url).netloc.lower()}"
        if origin in self._robots:
            return self._robots[origin]
        robots_url = self._robots_url(url)
        try:
            response = self._get(robots_url)
            if response.status_code == 404:
                self._robots[origin] = True
            elif response.status_code >= 400:
                self._robots[origin] = None
            else:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
                self._robots[origin] = parser.can_fetch(USER_AGENT, url)
        except requests.RequestException:
            self._robots[origin] = None
        return self._robots[origin]

    def fetch_configured(
        self,
        url: str,
        source: SourceDefinition,
        *,
        landing_page: bool,
    ) -> FetchResult:
        decision = check_source_policy(source, PolicyAction.FETCH)
        if not source.enabled:
            return FetchResult(False, url, reason=f"{source.name} is disabled.")
        if not decision.allowed:
            return FetchResult(False, url, reason=decision.reason)
        if not source_matches_url(source, url):
            return FetchResult(False, url, reason="URL is outside the configured source URL/RSS path.")
        robots = self.robots_allowed(url)
        if robots is False:
            return FetchResult(False, url, reason="robots.txt disallows this URL for the discovery client.")
        if robots is None and not landing_page:
            return FetchResult(False, url, reason="robots policy is unknown; only the configured public landing page is allowed.")
        result = self._fetch(url)
        if result.allowed and not source_matches_url(source, result.url):
            return FetchResult(False, result.url, reason="Redirect left the configured source URL/RSS path.")
        return result

    def fetch_unknown_landing(self, url: str) -> FetchResult:
        decision = check_unknown_public_landing(url)
        if not decision.allowed:
            return FetchResult(False, url, reason=decision.reason)
        robots = self.robots_allowed(url)
        if robots is False:
            return FetchResult(False, url, reason="robots.txt disallows this search-result landing page.")
        return self._fetch(url)

    def _fetch(self, url: str) -> FetchResult:
        try:
            response = self._get(url)
            response.raise_for_status()
        except requests.RequestException as exc:
            return FetchResult(False, url, reason=f"HTTP fetch failed: {exc}")
        content_type = response.headers.get("Content-Type", "").lower()
        if len(response.content) > MAX_PAGE_BYTES:
            return FetchResult(False, response.url, reason="Page exceeds the 2 MB discovery limit.")
        return FetchResult(True, response.url, response.text, content_type, "Fetched under source and robots policy.")
